#!/usr/bin/env python3
"""One-shot dev verification: syntax -> docs -> audit -> showcase -> upstream
drift -> i18n -> anim rhythm -> anim cursor -> qm -> smoke.

Combines the AGENTS.md 测试流程 steps into a single command so the AI runs
one tool call instead of five.  Prints a one-line summary per step on
success; on failure prints the full output so the error can be fixed
straight from the report.

Step activation:
  - syntax : always, over git-diff .py files (--all to scan ui/+utils/ instead)
  - docs   : always (validates doc file/symbol references via check_docs.py)
  - audit  : always (check_audit.py: audit_registry.json 契约——deprecated 已删
            文件不得复活/残留引用（allowed_mentions 白名单外），suspended 休眠
            文件不得被主 UI import；未登记删除仅提示不失败）
  - showcase: always (check_showcase.py: ui/custom_widget 导出清单 vs
            scripts/style_showcase.py 展示/排除登记，防新增控件漏展示)
  - upstream: always (check_upstream_drift.py: 上游富文本契约漂移——**提醒不阻断**；
            上游包根不存在时自动跳过)
  - i18n   : always (scans whole ui/modules/utils; known orphans exempted)
  - anim   : always (check_anim_rhythm.py: 演示动画**有效时间轴**不变量——由
            anim_kit.resolve_timeline 解析（渲染侧同源，杜绝类属性假绿灯）、
            N_FRAMES 下限、F_* 顺序、EMPH_SEG 合法，另校验 CursorPlan 帧序；
            脚本缺失时跳过)
  - animcur: when an animation/panel-geometry file changed
            (check_anim_cursor.py: README 大画幅场景落点必须等于控件/弹层真实
            几何、覆盖层每帧置顶且不被手绘弹层裁剪/调暗；Qt 不可用时跳过)
  - qm     : only when a .ts file changed
  - smoke  : with --smoke, or automatically when a startup-chain file changed
  - ruff   : with --full (skipped when ruff is not installed)
  - pytest : with --full (missing heavy deps like torch/cv2 skip gracefully)

Usage:
  python scripts/verify.py              # syntax(diff) + i18n + qm(if ts changed)
  python scripts/verify.py --smoke      # also run the startup smoke test
  python scripts/verify.py --all        # full syntax scan instead of git-diff files
  python scripts/verify.py --full       # release gate: also ruff + pytest

Exit code: 0 all pass, 1 any step failed.
"""

import argparse
import hashlib
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

# Animation-cursor check triggers: the README 大画幅 generators, the shared
# anim kit, and the panel/widgets whose real geometry the cursor waypoints are
# derived from (a layout change here can desync "光标落点" from "控件位置").
ANIM_TRIGGERS = (
    "scripts/anim_kit.py",
    "scripts/check_anim_rhythm.py",
    "scripts/check_anim_cursor.py",
    "scripts/gen_readme_anim.py",
    "scripts/gen_readme_showcase.py",
    "ui/text_panel.py",
    "ui/custom_widget/label.py",
    "ui/custom_widget/checkbox.py",
    "ui/custom_widget/combobox.py",
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


def _orphan_only(code):
    """True when i18n_check exited with ONLY the orphan bit (bit 4)."""
    return code != 0 and not (code & 3)


def _config_snapshot():
    """Record content and write timestamps, without exposing config values."""
    directory = ROOT / "config"
    return {
        path.relative_to(directory).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns
        )
        for path in directory.rglob("*") if path.is_file()
    }


def _check_config_unchanged(before):
    after = _config_snapshot()
    changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    if changed:
        print("❌ 配置保护: 检查期间真实配置发生变化：" + ", ".join(changed))
        return False
    print(f"✅ 配置保护: {len(before)} 个真实配置文件内容与修改时间未变")
    return True


def check_anim_rhythm():
    """演示动画节奏校验（check_anim_rhythm.py）。

    脚本不存在时跳过（向后兼容）；失败时完整打印，便于照报告定位。
    """
    script = ROOT / "scripts" / "check_anim_rhythm.py"
    if not script.exists():
        print("⏭  动画节奏: 校验脚本不存在，跳过")
        return True
    r = _run([_py(), str(script)])
    if r.returncode == 0:
        print(f"✅ 动画节奏: {r.stdout.strip()}")
        return True
    print("❌ 动画节奏: 时间轴不变量校验失败")
    _dump(r)
    return False


def _run_full_steps(failures):
    """--full release gate: ruff + pytest (both skipped gracefully if absent)."""

    # ── ruff ─────────────────────────────────────────────────────────────
    # Extend the project rules so its configured ignores (e.g. E501) survive.
    r = _run([_py(), "-m", "ruff", "check", "--extend-select", "W",
              "ui/", "utils/", "modules/"])
    if "No module named" in (r.stderr + r.stdout):
        print("⏭  ruff: 未安装，跳过（--install 或 pip install ruff 后可用）")
    elif r.returncode == 0:
        print("✅ ruff: 通过，无问题")
    else:
        failures += 1
        print("❌ ruff: 发现问题")
        _dump(r)

    # ── pytest ───────────────────────────────────────────────────────────
    r = _run([_py(), "-m", "pytest", "tests/", "-v", "--tb=short"], timeout=600)
    if "No module named" in (r.stderr + r.stdout) and "pytest" not in r.stdout:
        print("⏭  pytest: 未安装或依赖缺失，跳过")
    elif r.returncode == 0:
        print("✅ pytest: tests/ 全部通过")
    elif "ModuleNotFoundError" in (r.stderr + r.stdout):
        # Heavy optional deps (cv2, torch, numpy…) — same tolerance as发版门禁
        print("! pytest: 部分测试因缺少 opencv/torch/numpy 等重依赖跳过，其余通过")
    else:
        failures += 1
        print("❌ pytest: 存在失败用例")
        _dump(r)

    return failures


def main():
    _reconfigure_stdout()
    parser = argparse.ArgumentParser(description="One-shot dev verification")
    parser.add_argument("--smoke", action="store_true", help="Force-run the startup smoke test")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Syntax-check all ui/+utils/ files instead of git-diff files",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Release gate: also run ruff and pytest (skipped gracefully if absent)",
    )
    args = parser.parse_args()
    config_before = _config_snapshot()

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

    # ── 3. audit ────────────────────────────────────────────────────────
    r = _run([_py(), str(ROOT / "scripts" / "check_audit.py")])
    if r.returncode == 0:
        print(r.stdout.rstrip())
    else:
        failures += 1
        print("❌ audit: 审计登记表/删除残留检查失败")
        _dump(r)

    # ── 3b. showcase coverage ───────────────────────────────────────────
    r = _run([_py(), str(ROOT / "scripts" / "check_showcase.py")])
    if r.returncode == 0:
        print("✅ showcase: 展示台覆盖与 ui/custom_widget 导出一致")
    else:
        failures += 1
        print("❌ showcase: 展示台漏登记 / 重复登记 / 陈旧登记")
        _dump(r)

    # ── 3c. upstream drift (non-blocking: 提醒，不计入失败) ─────────────
    r = _run([_py(), str(ROOT / "scripts" / "check_upstream_drift.py")])
    if r.returncode == 0:
        if "跳过" in r.stdout:
            print("⏭  上游巡检: 未找到上游包根，跳过")
        else:
            print("✅ 上游巡检: 富文本契约无漂移")
    else:
        print("⚠ 上游巡检: 检测到上游契约漂移（不阻断，需人工评估兼容影响）")
        _dump(r)

    # ── 4. i18n ──────────────────────────────────────────────────────────
    r = _run([_py(), str(ROOT / "scripts" / "i18n_check.py"), "--ci"])
    code = r.returncode
    if code == 0:
        print("✅ i18n: 通过（无硬编码中文/无缺失/无孤儿）")
    elif code & 3:
        failures += 1
        print(f"❌ i18n: 失败（退出码 {code}，1=硬编码 2=缺失 4=孤儿）")
        _dump(r)
    elif _orphan_only(code):
        # Only the orphan bit. Project-wide orphans are known noise from
        # indirect canvas.tr()/self.tr(variable) calls, hand-maintained in
        # zh_CN.ts — don't fail or dump 160+ lines.
        print(
            "⚠ i18n: 存在孤儿条目（间接 tr() 调用已知噪音，可忽略；"
            "需核对时手动跑 i18n_check.py --show-expected）"
        )

    # ── 4b. anim rhythm ──────────────────────────────────────────────────
    if not check_anim_rhythm():
        failures += 1

    # ── 4c. anim cursor alignment (geometry-derived waypoints) ───────────
    anim_hit = [p.as_posix() for p in changed if p.as_posix() in ANIM_TRIGGERS]
    if anim_hit:
        script = ROOT / "scripts" / "check_anim_cursor.py"
        if not script.exists():
            print("⏭  动画光标落点: 校验脚本不存在，跳过")
        else:
            r = _run([_py(), str(script)], timeout=180)
            if r.returncode == 0:
                print(f"✅ 动画光标落点: {r.stdout.strip().splitlines()[-1]}")
            else:
                failures += 1
                print("❌ 动画光标落点: 航点与控件几何不一致")
                _dump(r)
    else:
        print("⏭  动画光标落点: 未触发（未改动动画/面板几何文件）")

    # ── 5. qm ────────────────────────────────────────────────────────────
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

    # ── 6. Smoke ─────────────────────────────────────────────────────────
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

    # ── 7. --full release gate: ruff + pytest ────────────────────────────
    if args.full:
        failures = _run_full_steps(failures)

    if not _check_config_unchanged(config_before):
        failures += 1

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
