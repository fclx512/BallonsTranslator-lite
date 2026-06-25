"""译文软换行整理。

提供 ``normalize_softbreaks`` 纯函数，按 ``「」`` 分块归一化译文中的无意义换行：

- 无任何 ``「」`` 的文本：整体 ``\\n``/空白压成单空格，交给排版器重排。
- ``「」`` 块内换行压成空格；块与块之间用单个 ``\\n`` 分隔（每句独占一行）。
- ``「」`` 块之间的纯文本片段：删掉换行、紧贴相邻引号块。
- 嵌套 ``「」`` 取最外层配对，内层字面保留。
- 幂等：对已整理过的文本再跑一次结果不变。

不依赖 Qt，仅用标准库，可被 ``utils`` 包或同级目录导入。
"""

import re

QUOTE_OPEN = "「"
QUOTE_CLOSE = "」"

# 连续 \n 及其前后的空格/制表符
_NEWLINE_RUN = re.compile(r"[ \t]*\n+[ \t]*")
_MULTIPLE_SPACES = re.compile(r" {2,}")


def normalize_softbreaks(text: str) -> str:
    """归一化译文软换行。见模块文档。"""
    if not isinstance(text, str) or text == "":
        return text

    if QUOTE_OPEN not in text:
        return _join_flat(text)

    segments = _split_by_outer_quotes(text)
    parts = []
    for typ, content in segments:
        if typ == "quote":
            parts.append(("quote", "「" + _join_in_quote(content) + "」"))
        else:
            parts.append(("plain", _join_plain(content)))

    # 去掉 strip 后为空的 plain 段（不阻断两侧 quote 的相邻关系）
    non_empty = [p for p in parts if not (p[0] == "plain" and p[1] == "")]

    out = []
    for i, (typ, content) in enumerate(non_empty):
        out.append(content)
        if i + 1 < len(non_empty) and typ == "quote" and non_empty[i + 1][0] == "quote":
            out.append("\n")
    return "".join(out)


def _join_flat(s: str) -> str:
    """无引号文本：连续 \\n/空白 → 单空格，strip。"""
    s = _NEWLINE_RUN.sub(" ", s)
    s = _MULTIPLE_SPACES.sub(" ", s)
    return s.strip()


def _join_in_quote(s: str) -> str:
    """quote 段内：连续 \\n/空白 → 单空格，strip。"""
    s = _NEWLINE_RUN.sub(" ", s)
    s = _MULTIPLE_SPACES.sub(" ", s)
    return s.strip()


def _join_plain(s: str) -> str:
    """plain 段：删除 \\n 及紧邻空白，strip。CJK 紧邻字符不留空格。"""
    s = _NEWLINE_RUN.sub("", s)
    s = _MULTIPLE_SPACES.sub(" ", s)
    return s.strip()


def _split_by_outer_quotes(text: str) -> list:
    """按最外层 ``「」`` 切分。返回 ``[(type, content), ...]``，type 为
    ``'quote'`` 或 ``'plain'``。嵌套 ``「」`` 取最外层，内层字面保留。
    未闭合的 ``「`` 取到字符串末尾。"""
    segments = []
    pos = 0
    n = len(text)
    while pos < n:
        if text[pos] == QUOTE_OPEN:
            depth = 1
            start = pos + 1
            pos += 1
            while pos < n and depth > 0:
                if text[pos] == QUOTE_OPEN:
                    depth += 1
                elif text[pos] == QUOTE_CLOSE:
                    depth -= 1
                pos += 1
            # depth==0 说明匹配到了 」，去掉外层「」；否则未闭合取到末尾
            inner = text[start:pos - 1] if depth == 0 else text[start:]
            segments.append(("quote", inner))
        else:
            start = pos
            while pos < n and text[pos] != QUOTE_OPEN:
                pos += 1
            segments.append(("plain", text[start:pos]))
    return segments