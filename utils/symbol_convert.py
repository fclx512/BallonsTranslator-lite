"""符号连字转换（软键盘式输入辅助）的纯逻辑层。

开启 ``utils/config.py::ProgramConfig`` 的 ``symbol_convert_enabled`` 后，
右栏文本编辑器在内容变化时把可合并的符号序列自动替换成单个连字符号
（两个感叹号 → ‼、叹问连写 → ⁉），替换范围在编辑器里短暂高亮提示。
编辑侧消费见 ``ui/textedit_area.py::SourceTextEdit._apply_symbol_conversion``。

映射表刻意保持最小集：只收漫画排版里真正通用的连字符号（‼／⁉）。
连写串按"两两合并"处理（``！！！``→``‼！``、``！！！！``→``‼‼``，
总数不变）；没有通用单字符号的连写（？？）和混合全半角（如 ``！!``）
一律不动——用户手打大概率是有意的。
"""

# 序列 → 连字符号。同一位置的候选按字典序尝试即可（当前全部等长 2，
# 若日后引入更长序列，把"长序列优先"的排序职责放进 find_conversions）。
SYMBOL_CONVERT_MAP = {
    "！！": "‼",
    "!!": "‼",
    "！？": "⁉",
    "？！": "⁉",
    "!?": "⁉",
    "?!": "⁉",
}


def find_conversions(text: str) -> list:
    """扫描文本，返回可替换段 ``[(start, end, replacement), ...]``。

    坐标为 ``text`` 的半开区间 ``[start, end)``；各段不重叠、按出现顺序
    排列。无可替换段返回空表。
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        for pattern, replacement in SYMBOL_CONVERT_MAP.items():
            end = i + len(pattern)
            if text.startswith(pattern, i):
                out.append((i, end, replacement))
                i = end
                break
        else:
            i += 1
    return out
