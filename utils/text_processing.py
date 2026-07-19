HALF2FULL = {i: i + 0xFEE0 for i in range(0x21, 0x7F)}
HALF2FULL[0x20] = 0x3000

FULL2HALF = dict((i + 0xFEE0, i) for i in range(0x21, 0x7F))
FULL2HALF[0x3000] = 0x20
FULL2HALF[0x3002] = 0x2E

LANGSET_CJK = {"简体中文", "繁體中文", "日本語"}


def full_len(s: str):
    """
    Convert all ASCII characters to their full-width counterpart.
    https://stackoverflow.com/questions/2422177/python-how-can-i-replace-full-width-characters-with-half-width-characters
    """
    return s.translate(HALF2FULL)


def half_len(s):
    """
    Convert full-width characters to ASCII counterpart
    """
    return s.translate(FULL2HALF)


def is_cjk(lang: str) -> bool:
    return lang in LANGSET_CJK
