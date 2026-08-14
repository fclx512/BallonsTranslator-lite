#!/usr/bin/env python3
"""校验文档里的文件路径 / 符号引用是否仍指向真实位置。

扫描 `AGENTS.md` 与 `docs/` 下「活文档」的反引号引用，识别两类可校验形式：

1. 仓库相对路径：`ui/configpanel.py`、`utils/config.py`
2. 符号引用：`ui/configpanel.py::DEFAULT_SHORTCUTS`（文件存在 + 符号可定位）

约定（见 AGENTS.md「文档规范」）：引用文件用仓库相对路径；引用符号用
`路径::符号` 取代易漂移的行号。

默认跳过（不校验）：
- 归档/日志类文档：`daily_log.md`、`经验教训.md`、`上游参考.md`
  —— 这些记录历史事实或引用上游仓库，路径随历史/跨库而变。
- 裸文件名（如 `config.json`、`快捷键.md`）—— 无目录前缀无法定位。
- 模板语言文件（`translate/xx.ts`、`translate/ja_JP.ts`）—— 示例而非真实文件。
- 上游仓库路径（`ballontranslator/`、`BallonsTranslator/`、`resources/`）—— 跨库。
- 围栏代码块、URL、被 gitignore 的运行时文件（`config/config.json`）。

用法：
  python scripts/check_docs.py             # 校验，失效引用退出码 1
  python scripts/check_docs.py --verbose   # 同时打印被跳过的引用

退出码：0 通过，1 存在失效引用。
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 活文档扫描根：AGENTS.md + docs/（排除下列归档/跨库文档）
SCAN_ROOTS = [ROOT / "AGENTS.md", ROOT / "docs"]
SKIP_DOCS = {
    "docs/daily_log.md",
    "docs/基础速查/经验教训.md",
    "docs/基础速查/上游参考.md",
}

# 运行时或 gitignore 才存在的路径，检查器视为合法缺失
KNOWN_ABSENT = {
    "config/config.json",
}

# 可识别的文件扩展名（小写）
FILE_EXTS = {
    "py", "md", "json", "css", "bat", "sh", "toml", "txt", "ts", "qm",
    "jsx", "svg", "xml", "ini", "cfg", "yaml", "yml", "png", "jpg",
}

# 仓库根下允许以裸文件名引用的文件（无需目录前缀）
ROOT_FILES = {
    "launch.py", "launch.bat", "pyproject.toml", "README.md", "AGENTS.md",
    "requirements.txt", "requirements_core.txt", ".gitignore",
}

# 跨仓库（上游 BallonsTranslator）路径前缀 —— 本仓库不存在，跳过
UPSTREAM_PREFIXES = ("ballontranslator/", "BallonsTranslator/", "resources/")

INLINE_CODE = re.compile(r"`([^`\n]+)`")
FENCE = re.compile(r"^\s*(```|~~~)")
FILEISH = re.compile(r"^[\w./-]+\.([A-Za-z0-9]+)$")
TRANSLATE_TPL = re.compile(r"^translate/[^/]+\.(ts|qm)$")


def _ext(fileish_token):
    m = FILEISH.match(fileish_token)
    return m.group(1).lower() if m else None


def is_fileish(token):
    return _ext(token) in FILE_EXTS


def _looks_like_url(token):
    return token.startswith(("http://", "https://", "www.", "ftp://"))


def _real_translate_files():
    d = ROOT / "translate"
    if not d.is_dir():
        return set()
    return {p.relative_to(ROOT).as_posix() for p in d.glob("*.ts")} | {
        p.relative_to(ROOT).as_posix() for p in d.glob("*.qm")
    }


def symbol_in_file(symbol, text):
    """符号是否存在：非字母数字下划线边界上的独立标识符。"""
    return (
        re.search(
            r"(?<![A-Za-z0-9_])" + re.escape(symbol) + r"(?![A-Za-z0-9_])",
            text,
        )
        is not None
    )


def resolve_path(token, real_translate):
    """把仓库相对路径解析为绝对 Path；无法/无需定位返回 None。"""
    if token in KNOWN_ABSENT:
        return None  # 已知合法缺失
    if TRANSLATE_TPL.match(token) and token not in real_translate:
        return None  # 模板语言文件（示例）
    if token.startswith(UPSTREAM_PREFIXES):
        return None  # 跨仓库上游路径
    if "/" in token:
        return ROOT / token
    if token in ROOT_FILES:
        return ROOT / token
    return None  # 裸文件名，无法可靠定位


def _skip_doc(rel):
    if rel.as_posix() in SKIP_DOCS:
        return True
    return False


def iter_md_files():
    for root in SCAN_ROOTS:
        if root.is_file():
            yield root
        elif root.is_dir():
            for p in sorted(root.rglob("*.md")):
                if ".obsidian" in p.parts:
                    continue
                if _skip_doc(p.relative_to(ROOT)):
                    continue
                yield p


def scan():
    """返回 (broken, skipped)，元素为 (文件, 行号, token, 原因)。"""
    broken = []
    skipped = []
    real_translate = _real_translate_files()
    for md in iter_md_files():
        try:
            text = md.read_text(encoding="utf-8")
        except Exception as exc:  # 编码异常等，视为失效可读
            broken.append((md, 0, "<unreadable>", f"读取失败: {exc}"))
            continue
        in_fence = False
        for lineno, line in enumerate(text.splitlines(), 1):
            if FENCE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue  # 忽略围栏代码块里的示意路径
            for token in INLINE_CODE.findall(line):
                token = token.strip()
                if not token or _looks_like_url(token):
                    continue
                if "::" in token:
                    path_part, symbol = token.split("::", 1)
                    symbol = symbol.strip().rstrip("()")
                    if not symbol:
                        continue
                    if not is_fileish(path_part.strip()):
                        # 占位示例（如 `路径::符号`），非真实文件引用，跳过
                        skipped.append((md, lineno, token, "占位符号示例，跳过"))
                        continue
                    p = resolve_path(path_part.strip(), real_translate)
                    if p is None or not p.is_file():
                        broken.append((md, lineno, token, "文件不存在"))
                        continue
                    content = p.read_text(encoding="utf-8", errors="replace")
                    if not symbol_in_file(symbol, content):
                        broken.append((md, lineno, token, f"符号 `{symbol}` 未找到"))
                elif is_fileish(token):
                    p = resolve_path(token, real_translate)
                    if p is None:
                        skipped.append((md, lineno, token, "裸文件名/模板/跨库，跳过"))
                    elif not p.is_file():
                        broken.append((md, lineno, token, "文件不存在"))
    return broken, skipped


def main():
    parser = argparse.ArgumentParser(description="校验文档路径/符号引用")
    parser.add_argument("--verbose", action="store_true", help="同时打印被跳过的引用")
    args = parser.parse_args()

    broken, skipped = scan()

    if broken:
        print(f"❌ docs: {len(broken)} 处失效引用：")
        for md, lineno, token, reason in broken:
            rel = md.relative_to(ROOT)
            print(f"  {rel}:{lineno}  `{token}`  → {reason}")
        if skipped:
            print(f"  （另有 {len(skipped)} 处被跳过，--verbose 查看）")
        sys.exit(1)

    total = sum(1 for _ in iter_md_files())
    print(f"✅ docs: {total} 个活文档，路径/符号引用全部有效")
    if args.verbose and skipped:
        print("  --verbose 跳过的引用：")
        for md, lineno, token, reason in skipped:
            rel = md.relative_to(ROOT)
            print(f"  {rel}:{lineno}  `{token}`  → {reason}")


if __name__ == "__main__":
    main()
