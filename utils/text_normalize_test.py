"""Test normalize_softbreaks — 手动执行而非 pytest。

运行：cd d:/ruanjian/BallonsTranslator-lite/utils && python text_normalize_test.py
"""
from text_normalize import normalize_softbreaks


def test_no_newline():
    assert normalize_softbreaks("Hello world") == "Hello world"


def test_no_quotes_all_newlines():
    assert normalize_softbreaks("a\nb\nc") == "a b c"


def test_single_quote_pair():
    result = normalize_softbreaks("「行一\n行二」")
    assert result == "「行一 行二」"


def test_multi_quote_pairs():
    result = normalize_softbreaks("「句一\n继续」\n「句二」")
    assert result == "「句一 继续」\n「句二」"


def test_mixed_quote_and_plain():
    result = normalize_softbreaks("「只\n是」没有\n问题")
    assert result == "「只 是」没有问题"


def test_nested_quotes():
    result = normalize_softbreaks("「外层「内层」继续\n第二行」")
    assert result == "「外层「内层」继续 第二行」"


def test_consecutive_newlines():
    result = normalize_softbreaks("a\n\nb")
    assert result == "a b"


def test_idempotent():
    once = normalize_softbreaks("「行一\n行二」\n「行三」")
    twice = normalize_softbreaks(once)
    assert once == twice


def test_empty_string():
    assert normalize_softbreaks("") == ""


def test_only_whitespace():
    result = normalize_softbreaks("  \n  ")
    assert result == "" or result == "  "  # 看你抉择：可剥或保留


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(
        [(n, f) for n, f in globals().items() if n.startswith("test_")]
    ):
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            print(f"  FAIL  {name}: {e}")
            failed += 1
    print("Done." + ("" if failed == 0 else f" ({failed} failed)"))