#!/usr/bin/env python3
"""审计登记表：死代码/休眠代码 + 删除文件残留引用。

读取 `scripts/audit_registry.json`（登记表，机器可查的"固定策略"），执行三类检查：

1. **deprecated（已删待守）**——登记的文件必须已不存在；全仓语料（活文档 +
   代码）对它的引用必须落在该条的 `allowed_mentions` 白名单内，否则报错：
   防"死代码复活"、防"删除后残留引用无人处理"（TextStyleDialog /
   scene_textlayout 教训）。
2. **suspended（休眠）**——登记的文件必须存在；`ui/` 内（text_engine 之外，
   即主 UI）不得 import 它：防休眠代码被悄悄唤醒产生维护负担。
3. **git 已删但未登记**——仅提示不失败：删除要"声明"（登记 deprecated）才
   纳入残留审计；批次进行中的删除不阻塞。

退出码：0 全部通过（可能有提示行）；1 任一检查失败。
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "scripts" / "audit_registry.json"

# 语料豁免：归档日志、发版产物、登记表自身及检查器（两者合法引用已删文件名）
SKIP_DIRS = {
    ".git",
    ".zcode",  # 会话计划等本地产物，非仓库内容
    ".claude",
    "__pycache__",
    "ballontrans_pylibs_win",
    "release",
    "data",
    "node_modules",
    ".obsidian",
}
SKIP_FILES = {
    "docs/daily_log.md",
    "docs/基础速查/经验教训.md",
    "docs/基础速查/上游参考.md",
    "manifest.json",  # 发版产物，重生成前天然滞后
    "scripts/audit_registry.json",
    "scripts/check_audit.py",  # 自身 docstring 会示范已删文件名
}
SCAN_EXTS = {".py", ".md", ".json", ".txt", ".css"}


def _load_registry():
    if not REGISTRY.is_file():
        print("❌ audit: scripts/audit_registry.json 不存在")
        sys.exit(1)
    try:
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"❌ audit: 登记表不可读: {exc}")
        sys.exit(1)


def corpus_files():
    """全部可审计文件：(Path, 相对路径 posix)。"""
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            rel = p.relative_to(ROOT).as_posix()
            if p.suffix not in SCAN_EXTS or rel in SKIP_FILES:
                continue
            yield p, rel


def active_ui_files():
    """ui/ 下非 text_engine 的 .py（主 UI，休眠文件不可被其引用）。"""
    ui_root = ROOT / "ui"
    for dirpath, dirnames, filenames in os.walk(ui_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if Path(dirpath).relative_to(ROOT).as_posix().startswith("ui/text_engine"):
            dirnames[:] = []
            continue
        for fn in filenames:
            if fn.endswith(".py"):
                yield Path(dirpath) / fn


def deprecated_refs(path, allowed_mentions):
    """返回对已删文件 path 的残留引用 [(rel, lineno, token)]，白名单内文件除外。"""
    posix = path.replace("\\", "/")
    name = Path(path).name
    # 全路径形态 ui/x.py
    path_re = re.compile(re.escape(posix))
    # 文件名形态 x.py，整词匹配：test_annotation_controls.py 不算引用 controls.py
    name_re = re.compile(rf"(?<![A-Za-z0-9_.]){re.escape(name)}(?![A-Za-z0-9_])")
    # 模块引用形态：import controls / from .controls import X。
    # 不做裸词匹配——常见英文作文件名的模块（如 controls.py）会被
    # self.controls、docstring 散文误判为残留引用。
    # from 分支要求后随 import（from X.controls import Y），避免误伤
    # "yield from panel.iter_controls()" 这类生成器 from。
    stem, ext = os.path.splitext(name)
    mod_re = (
        re.compile(
            rf"(?:from\s+[\w.]*\.?{re.escape(stem)}\s+import|import\s+[\w.]*\.?{re.escape(stem)})(?![A-Za-z0-9_])"
        )
        if ext == ".py"
        else None
    )
    allowed = set(allowed_mentions or ())
    hits = []
    for p, rel in corpus_files():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if path_re.search(line) or name_re.search(line) or (
                mod_re is not None and mod_re.search(line)
            ):
                if rel not in allowed:
                    hits.append((rel, lineno, name))
    return hits


def suspended_imports(path):
    """返回主 UI 对休眠文件 path 的 import 引用 [(rel, lineno, token)]。"""
    parts = path.replace("\\", "/").split("/")
    full = ".".join(parts)[:-3]  # ui.text_engine.editing.manager
    tail = ".".join(parts[-2:])[:-3]  # editing.manager（engine 内部相对引用形态）
    pats = [
        re.compile(rf"(?<![A-Za-z0-9_]){re.escape(full)}(?![A-Za-z0-9_])"),
        re.compile(rf"(?<![A-Za-z0-9_]){re.escape(tail)}(?![A-Za-z0-9_])"),
    ]
    hits = []
    for p in active_ui_files():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = p.relative_to(ROOT).as_posix()
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(pat.search(line) for pat in pats):
                hits.append((rel, lineno, tail))
    return hits


def git_deleted_not_registered(registry):
    """工作区已删但未在登记表声明的文件列表（仅提示）。"""
    try:
        out = subprocess.run(
            ["git", "-c", "core.quotepath=false", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    deleted = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:]
        if " -> " in path:
            continue  # 重命名，目标方不是删除
        if code in ("D ", " D"):
            deleted.append(path.strip().strip('"'))
    registered = set(registry.get("deprecated", {})) | set(
        registry.get("suspended", {})
    )
    return [d for d in deleted if d not in registered]


def main():
    registry = _load_registry()
    problems = []
    notes = []

    # 1. deprecated：不得复活，残留引用必须清零（白名单外）
    deprecated = registry.get("deprecated", {})
    for path in sorted(deprecated):
        if (ROOT / path).exists():
            problems.append(f"{path}: 登记为 deprecated 但文件存在（死代码复活或忘删？）")
            continue
        info = deprecated[path] or {}
        hits = deprecated_refs(path, info.get("allowed_mentions"))
        for rel, lineno, tok in hits:
            problems.append(
                f"{path}: 删除后仍有白名单外引用 → {rel}:{lineno}  `{tok}`"
            )

    # 2. suspended：必须存在，且主 UI 不得 import
    suspended = registry.get("suspended", {})
    for path in sorted(suspended):
        if not (ROOT / path).is_file():
            problems.append(
                f"{path}: suspended 登记但文件不存在（删除应移到 deprecated）"
            )
            continue
        for rel, lineno, tok in suspended_imports(path):
            problems.append(
                f"{path}: 被主 UI 引用 → {rel}:{lineno}  `{tok}`（休眠代码被唤醒？）"
            )

    # 3. git 已删未登记：提示，不阻塞（批次进行中的删除不纳入审计）
    for d in git_deleted_not_registered(registry):
        notes.append(
            f"⚠ 未登记删除 {d}：在 audit_registry.json 登记 deprecated 后启用残留审计"
        )

    if problems:
        print(f"❌ audit: {len(problems)} 处问题：")
        for p in problems:
            print("  " + p)
        for n in notes:
            print(n)
        sys.exit(1)

    print(
        f"✅ audit: 登记表 {len(deprecated)} 居删 / {len(suspended)} 休眠 检查通过"
    )
    for n in notes:
        print(n)


if __name__ == "__main__":
    main()