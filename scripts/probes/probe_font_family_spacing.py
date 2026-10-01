"""真机验收：家族名空格变体归一（2026-10-01 修「上游项目转 lite 画布回退宋体」）。

跑法（必须真机 windows 平台，offscreen 无字体库测不了匹配）：
  QT_QPA_PLATFORM=windows ./ballontrans_pylibs_win/python.exe \
      scripts/probes/probe_font_family_spacing.py

验收判据：
  1. 全字体库不误伤——对 QFontDatabase 里每个注册家族名，归一解析必须
     原样返回自身（修名字不许修丢任何已装字体）；
  2. 空白/大小写变体必须全部映射到某个真实注册家族（防失配）；
  3. 模拟上游项目数据（剥空格名）经 qfont_with_family → QFontInfo 解析
     回正确字体；
  4. 富文本 HTML 路径：load_rich_text_html 后失配名被归一为注册名。
"""

import os
import sys

os.environ["QT_QPA_PLATFORM"] = "windows"

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

from qtpy.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

from qtpy.QtGui import QFont, QFontDatabase, QFontInfo, QTextDocument  # noqa: E402

from ui.text_engine.annotations import load_rich_text_html  # noqa: E402
from ui.text_engine.font_family import (  # noqa: E402
    font_family_for_qt,
    qfont_with_family,
)


def main():
    families = list(QFontDatabase.families())
    spaced = [f for f in families if f != f.strip()]
    print(f"Qt 注册家族 {len(families)} 个，其中注册名自带首尾空格的 {len(spaced)} 个")
    for f in spaced:
        print(f"  {f!r}")

    failures = 0

    def check(cond, msg):
        nonlocal failures
        if not cond:
            failures += 1
            print(f"  [FAIL] {msg}")

    print("\n== 判据 1：全字体库身份解析（不误伤） ==")
    bad = [f for f in families if font_family_for_qt(f) != f]
    check(not bad, f"身份解析被改写的家族: {bad[:5]}")
    print(f"  {len(families) - len(bad)}/{len(families)} 原样通过")

    print("\n== 判据 2：空白/大小写变体全部可解析 ==")
    fam_set = set(families)
    unresolvable = []
    for f in families:
        variants = [f.strip(), f + " ", f.casefold()]
        if f[-1].isspace():
            variants.append(f[:-1])
        for v in variants:
            if font_family_for_qt(v) not in fam_set:
                unresolvable.append((f, v))
    check(not unresolvable, f"解析不到注册名的变体: {unresolvable[:5]}")
    print("  全部变体可解析" if not unresolvable else f"  {len(unresolvable)} 个失败")

    if spaced:
        print("\n== 判据 3：上游项目数据形态（剥空格名）经 qfont_with_family ==")
        for f in spaced[:4]:
            direct = QFontInfo(QFont(f.strip())).family()
            via = QFontInfo(qfont_with_family(QFont(), f.strip())).family()
            print(f"  {f.strip()!r}")
            print(f"    直接喂 QFont（修前行为）: {direct!r}")
            print(f"    qfont_with_family（修后）: {via!r}")
            check(via == f, f"应解析回 {f!r}, 得到 {via!r}")

        print("\n== 判据 4：富文本 HTML 路径归一 ==")
        target = spaced[0]
        doc = QTextDocument()
        load_rich_text_html(
            doc, '<p style="font-family:\'' + target.strip() + '\'">字</p>'
        )
        frag = doc.firstBlock().begin().fragment()
        got = frag.charFormat().font().family()
        print(f"  HTML 嵌 {target.strip()!r} -> 片段解析 {got!r}")
        check(got == target, f"应归一为 {target!r}")

    print("\n" + ("✅ 全部判据通过" if not failures else f"❌ {failures} 项失败"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
