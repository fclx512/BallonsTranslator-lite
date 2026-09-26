"""符号连字转换的纯逻辑用例（``utils/symbol_convert.py``）。

只钉映射表行为本身（用户实测反馈会围绕"哪些序列被换成了什么"），
编辑器侧的高亮与撤销交互不在此覆盖。
"""

import os.path
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.symbol_convert import find_conversions


class FindConversionsTest(unittest.TestCase):
    def test_fullwidth_sequences(self):
        self.assertEqual(
            find_conversions("真的！！"),
            [(2, 4, "‼")],
        )
        self.assertEqual(
            find_conversions("什么！？"),
            [(2, 4, "⁉")],
        )
        # 问在前也一样转（⁉ 字形为叹前问后，漫画惯例两者通用）
        self.assertEqual(
            find_conversions("什么？！"),
            [(2, 4, "⁉")],
        )

    def test_halfwidth_sequences(self):
        self.assertEqual(find_conversions("OK!!"), [(2, 4, "‼")])
        self.assertEqual(find_conversions("huh?!"), [(3, 5, "⁉")])

    def test_runs_merge_pairwise(self):
        # 连写串两两合并、总数不变：三连留单个、四连成对
        self.assertEqual(find_conversions("！！！"), [(0, 2, "‼")])
        self.assertEqual(find_conversions("！！！！"), [(0, 2, "‼"), (2, 4, "‼")])
        # 问号连写没有通用单字连字符号，不动
        self.assertEqual(find_conversions("？？"), [])

    def test_non_overlapping_left_to_right(self):
        # "！？！？" 两段各转一次，不重叠
        self.assertEqual(
            find_conversions("！？！？"),
            [(0, 2, "⁉"), (2, 4, "⁉")],
        )

    def test_plain_text_no_hits(self):
        self.assertEqual(find_conversions("普通的文本。"), [])
        self.assertEqual(find_conversions(""), [])
        # 连字符号自身不再参与任何序列，转换结果幂等
        self.assertEqual(find_conversions("‼⁉"), [])


if __name__ == "__main__":
    unittest.main()
