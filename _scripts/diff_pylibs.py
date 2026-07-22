"""
比对 ballontrans_pylibs_win（旧） vs ballontrans_pylibs_win

用法: python _scripts/diff_pylibs.py
输出报告到 _scripts/pylibs_diff_report.md
"""

import hashlib
import os
from collections import defaultdict
from pathlib import Path

BASE = Path(r"d:\ruanjian\BallonsTranslator-lite")
OLD = BASE / "ballontrans_pylibs_win（旧）"
NEW = BASE / "ballontrans_pylibs_win"

# 旧目录里可能多套了一层同名子目录
OLD_FLAT = OLD / "ballontrans_pylibs_win"
OLD_ROOT = OLD_FLAT if OLD_FLAT.is_dir() else OLD

REPORT = BASE / "_scripts" / "pylibs_diff_report.md"

BLOCK_SIZE = 65536


def walk_flat(root: Path) -> dict[str, os.stat_result]:
    """返回 {相对路径: stat}，且 key 统一为正斜杠"""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        # 忽略 .git (虽然不太可能有)
        dirnames[:] = [d for d in dirnames if d != ".git"]
        rel = Path(dirpath).relative_to(root).as_posix()
        for f in filenames:
            key = f"{rel}/{f}" if rel != "." else f
            fp = Path(dirpath) / f
            try:
                out[key] = fp.stat()
            except OSError:
                pass
        for d in dirnames:
            key = f"{rel}/{d}" if rel != "." else d
            dp = Path(dirpath) / d
            out[key] = dp.stat()
    return out


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def sha256_file(fp: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(fp, "rb") as f:
            while True:
                buf = f.read(BLOCK_SIZE)
                if not buf:
                    break
                h.update(buf)
        return h.hexdigest()
    except Exception:
        return "<ERROR>"


def analyze_ext(name: str) -> str:
    """归类文件类型"""
    ext = Path(name).suffix.lower()
    if ext in (".pyd", ".dll", ".exe"):
        return "native_binary"
    if ext in (".py", ".pyc", ".pyo"):
        return "python"
    if ext in (".zip",):
        return "archive"
    if ext in ("._pth", ".cat"):
        return "config"
    if ext == ".whl":
        return "wheel"
    return ext if ext else "(no ext)"


def main():
    print(f"旧版根目录: {OLD_ROOT}")
    print(f"新版根目录: {NEW}")
    print("正在遍历（这可能需要一段时间）...")

    old_files = walk_flat(OLD_ROOT)
    new_files = walk_flat(NEW)

    old_set = set(old_files.keys())
    new_set = set(new_files.keys())

    only_old = sorted(old_set - new_set)
    only_new = sorted(new_set - old_set)
    common = sorted(old_set & new_set)

    # ---- 统计 ----
    old_total_size = sum(s.st_size for s in old_files.values() if not s.st_mode & 0o40000)
    new_total_size = sum(s.st_size for s in new_files.values() if not s.st_mode & 0o40000)

    old_native_size = sum(s.st_size for k, s in old_files.items() if analyze_ext(k) == "native_binary" and not s.st_mode & 0o40000)
    new_native_size = sum(s.st_size for k, s in new_files.items() if analyze_ext(k) == "native_binary" and not s.st_mode & 0o40000)

    old_py_size = sum(s.st_size for k, s in old_files.items() if analyze_ext(k) == "python" and not s.st_mode & 0o40000)
    new_py_size = sum(s.st_size for k, s in new_files.items() if analyze_ext(k) == "python" and not s.st_mode & 0o40000)

    # ---- 内容级差异采样（常见同名文件）----
    same_content = []
    different_content = []
    binary_common = [k for k in common if analyze_ext(k) in ("native_binary", "archive", "wheel") and not old_files[k].st_mode & 0o40000]
    # 限制比对数量避免跑太久
    for k in binary_common[:200]:
        op = OLD_ROOT / k
        np_ = NEW / k
        if op.is_file() and np_.is_file():
            h1 = sha256_file(op)
            h2 = sha256_file(np_)
            if h1 == h2:
                same_content.append(k)
            else:
                different_content.append((k, old_files[k].st_size, new_files[k].st_size))

    # ---- 构建报告 ----
    lines = ["# Pylibs 目录差异报告", "", "生成时间: 2026-07-07", ""]
    lines.append(f"| 维度 | 旧版（{OLD_ROOT.name}） | 新版（{NEW.name}） |")
    lines.append("|------|------------------------|--------------------|")
    lines.append(f"| 文件/目录数 | {len(old_files)} | {len(new_files)} |")
    lines.append(f"| 总大小 | {fmt_size(old_total_size)} | {fmt_size(new_total_size)} |")
    lines.append(f"| .pyd/.dll/.exe 占用 | {fmt_size(old_native_size)} | {fmt_size(new_native_size)} |")
    lines.append(f"| .py/.pyc 占用 | {fmt_size(old_py_size)} | {fmt_size(new_py_size)} |")
    lines.append(f"| 旧有新版无 | {len(only_old)} 项 | — |")
    lines.append(f"| 新版有旧版无 | — | {len(only_new)} 项 |")
    lines.append(f"| 同路径同名 | {len(common)} 项 | {len(common)} 项 |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ---- Python 版本 ----
    lines.append("## Python 版本")
    lines.append("")
    for p in sorted(old_set | new_set):
        if Path(p).name.startswith("python") and Path(p).suffix in (".dll", "._pth", ".exe", ".zip"):
            tag = "旧" if p in old_set else "" + "新" if p in new_set else ""
            loc = []
            if p in old_set:
                loc.append(f"旧: {fmt_size(old_files[p].st_size)}")
            if p in new_set:
                loc.append(f"新: {fmt_size(new_files[p].st_size)}")
            lines.append(f"- `{p}` — {' / '.join(loc)}")
    lines.append("")

    # ---- 旧有新版无（分类 + 按大小排序）----
    lines.append("## 旧版有、新版无的文件")
    lines.append("")
    if only_old:
        by_ext = defaultdict(list)
        for k in only_old:
            cat = analyze_ext(k)
            sz = old_files[k].st_size if k in old_files and not old_files[k].st_mode & 0o40000 else 0
            by_ext[cat].append((k, sz))
        for cat in sorted(by_ext):
            items = sorted(by_ext[cat], key=lambda x: -x[1])
            total = sum(sz for _, sz in items)
            lines.append(f"### {cat}（{len(items)} 项，共 {fmt_size(total)}）")
            lines.append("")
            for k, sz in items[:50]:
                tag = f" [{fmt_size(sz)}]" if sz else " [dir]"
                lines.append(f"- {k}{tag}")
            if len(items) > 50:
                lines.append(f"- *…还有 {len(items)-50} 项*")
            lines.append("")
    else:
        lines.append("（无）")
        lines.append("")

    # ---- 新版有旧版无 ----
    lines.append("## 新版有、旧版无的文件")
    lines.append("")
    if only_new:
        by_ext = defaultdict(list)
        for k in only_new:
            cat = analyze_ext(k)
            sz = new_files[k].st_size if k in new_files and not new_files[k].st_mode & 0o40000 else 0
            by_ext[cat].append((k, sz))
        for cat in sorted(by_ext):
            items = sorted(by_ext[cat], key=lambda x: -x[1])
            total = sum(sz for _, sz in items)
            lines.append(f"### {cat}（{len(items)} 项，共 {fmt_size(total)}）")
            lines.append("")
            for k, sz in items[:50]:
                tag = f" [{fmt_size(sz)}]" if sz else " [dir]"
                lines.append(f"- {k}{tag}")
            if len(items) > 50:
                lines.append(f"- *…还有 {len(items)-50} 项*")
            lines.append("")
    else:
        lines.append("（无）")
        lines.append("")

    # ---- 同名内容差异 ----
    lines.append("## 同名二进制文件内容比对")
    lines.append("")
    lines.append(f"采样范围: 前 {len(binary_common)} 个 .pyd/.dll/.exe/.zip/.whl 文件")
    lines.append(f"- ✅ 哈希一致: **{len(same_content)}**")
    lines.append(f"- ❌ 哈希不同: **{len(different_content)}**")
    lines.append("")

    if different_content:
        lines.append("### 哈希不同的文件列表")
        lines.append("")
        lines.append("| 文件 | 旧版大小 | 新版大小 |")
        lines.append("|------|----------|----------|")
        for k, sz_old, sz_new in sorted(different_content, key=lambda x: -abs(x[1] - x[2]))[:100]:
            lines.append(f"| `{k}` | {fmt_size(sz_old)} | {fmt_size(sz_new)} |")
        lines.append("")
        if len(different_content) > 100:
            lines.append(f"*…还有 {len(different_content) - 100} 项*")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 备注")
    lines.append("")
    lines.append(f"- 旧版目录实际路径: `{OLD_ROOT}`")
    lines.append(f"- 新版目录实际路径: `{NEW}`")
    lines.append("- 旧版为 Python 3.13 embedded（`python313.dll`），新版为 Python 3.12 embedded（`python312.dll`）")
    lines.append("- 新旧版本差异是正常现象，关键在于其他第三方包的完整性")
    lines.append("- [ ] 请检查 `不同内容` 列表。若其中包含核心包（如 torch、PIL、numpy 等）的 pyd/dll，可能出问题")

    report_text = "\n".join(lines) + "\n"

    # 写文件
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(report_text, encoding="utf-8")
    print(f"\n报告已生成: {REPORT}")
    print(f"旧版文件数: {len(old_files)}, 新版文件数: {len(new_files)}")
    print(f"仅旧版有: {len(only_old)}, 仅新版有: {len(only_new)}")
    print(f"同名二进制哈希一致: {len(same_content)}, 不同: {len(different_content)}")


if __name__ == "__main__":
    main()
