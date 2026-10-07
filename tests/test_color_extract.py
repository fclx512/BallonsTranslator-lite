"""``utils/color_extract.py`` 的取色契约回归（合成图，不依赖模型/网络）。

钉住会静默失效的契约：

1. 黑字白边（同族无彩）→ 取黑（更暗的簇），不能取成白边颜色；
2. 彩字白边 → 取彩色（彩度高的簇为填充）；
3. 取出的颜色与周围背景几乎同色 → 直接判无效（掩膜只圈到背景时不许
   落一个「看不见的文字色」）；
4. 全书扫描聚类：半径是 LAB 阈值、代表色取众数（真实观测值而非均值，
   否则精确相等注入不命中）、count 数块数、样本有序并受 max_samples 截断。

前三条对「有概率图 / 无概率图」两种掩膜来源各跑一遍：合成概率图（对应
检测器 DBNet 概率图路径）与无概率图（经典二值化兜底路径）。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_color_extract.py -q
"""

import os
import os.path as osp
import sys
import unittest
from types import SimpleNamespace

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)

import numpy as np  # noqa: E402

from utils.color_extract import (  # noqa: E402
    CLUSTER_RADIUS,
    ColorSample,
    cluster_font_colors,
    extract_block_colors,
    scan_project_colors,
)


def _page(bg_rgb, fill_rgb, stroke_rgb):
    """背景 + 四道笔画（各带描边），返回 (整页图, 同尺寸概率图)。RGB 口径。"""
    img = np.full((240, 240, 3), bg_rgb, np.uint8)
    prob = np.zeros((240, 240), np.float32)
    stroke_px = 6
    for i in range(4):
        x, y, w, h = 40 + i * 40, 60, 18, 120
        img[y - stroke_px : y + h + stroke_px, x - stroke_px : x + w + stroke_px] = (
            stroke_rgb
        )
        img[y : y + h, x : x + w] = fill_rgb
        prob[y - stroke_px : y + h + stroke_px, x - stroke_px : x + w + stroke_px] = 0.9
        prob[y : y + h, x : x + w] = 1.0
    return img, prob


class FillColorPickingTest(unittest.TestCase):
    """每条用例都对「有概率图 / 无概率图」两种掩膜来源各跑一遍。"""

    def _extract_both(self, img, prob, box):
        return [
            extract_block_colors(img, box, prob=prob),
            extract_block_colors(img, box, prob=None),
        ]

    def test_black_fill_white_stroke_picks_black(self):
        img, prob = _page((120, 200, 240), (0, 0, 0), (255, 255, 255))
        for res in self._extract_both(img, prob, [20, 40, 220, 200]):
            self.assertIsNotNone(res)
            self.assertLess(max(res.frgb), 60, res)  # 黑填充，不是白描边

    def test_colored_fill_white_stroke_picks_color(self):
        img, prob = _page((200, 200, 200), (250, 120, 240), (255, 255, 255))
        for res in self._extract_both(img, prob, [20, 40, 220, 200]):
            self.assertIsNotNone(res)
            self.assertGreater(res.frgb[2], 180, res)  # 粉填充的 R/B 高
            self.assertLess(res.frgb[1], 180, res)

    def test_mask_equals_background_is_rejected(self):
        """整块只有纯色背景、没有文字：不许产出与背景同色的「文字色」。"""
        img = np.full((120, 120, 3), (200, 210, 180), np.uint8)
        prob = np.ones((120, 120), np.float32)
        for res in self._extract_both(img, prob, [10, 10, 110, 110]):
            self.assertIsNone(res)

    def test_tiny_block_is_rejected(self):
        img = np.full((60, 60, 3), (30, 30, 30), np.uint8)
        for res in self._extract_both(img, None, [0, 0, 3, 3]):
            self.assertIsNone(res)


class ChannelOrderTest(unittest.TestCase):
    """通道口径回归（2026-10-03 真机教训：应用管线 read_img 给 RGB，提取器
    早期按 BGR 设计，探针用 cv2.imread 掩盖了口径差 → 颜色整体反转）。"""

    def test_red_text_rgb_in_rgb_out(self):
        # 红字灰底（RGB 语义）：红=高 R、低 B。若通道反了会取成「蓝」。
        img, prob = _page((180, 180, 180), (200, 30, 30), (255, 255, 255))
        res = extract_block_colors(img, [20, 40, 220, 200], prob=prob)
        self.assertIsNotNone(res)
        self.assertGreater(res.frgb[0], 140, res)  # R 高
        self.assertLess(res.frgb[2], 90, res)  # B 低

    def test_palette_snap_overrides_picked_color(self):
        # 启发式取到近蓝，调板里有权威蓝：吸附后必须精确等于调板色。
        img, prob = _page((200, 200, 200), (20, 25, 210), (255, 255, 255))
        palette = [[27, 25, 214]]  # #1b19d6（用户人工采样）
        res = extract_block_colors(img, [20, 40, 220, 200], prob=prob, palette=palette)
        self.assertIsNotNone(res)
        self.assertEqual(res.frgb, [27, 25, 214], res)

    def test_palette_does_not_steal_achromatic_pick(self):
        # 黑字白边 + 调板含白色（别处的白字）：不许把白描边反吸成填充。
        img, prob = _page((120, 200, 240), (0, 0, 0), (255, 255, 255))
        palette = [[255, 255, 255]]
        res = extract_block_colors(img, [20, 40, 220, 200], prob=prob, palette=palette)
        self.assertIsNotNone(res)
        self.assertLess(max(res.frgb), 60, res)

    def test_palette_rescues_bubble_white_mispick(self):
        # 白气泡底上淡彩字：启发式误挑白底（低彩簇）时，调板里的字色
        # 经由有彩候选簇把它救回来。
        img, prob = _page((245, 245, 245), (190, 160, 90), (255, 255, 255))
        palette = [[190, 160, 90]]
        res = extract_block_colors(img, [20, 40, 220, 200], prob=prob, palette=palette)
        self.assertIsNotNone(res)
        self.assertEqual(res.frgb, [190, 160, 90], res)


def _sample(page="p1", index=0, rect=None):
    return ColorSample(pagename=page, block_index=index, rect=rect or [0, 0, 10, 10])


class ClusterFontColorsTest(unittest.TestCase):
    """候选色聚类：半径语义、众数代表色、计数与样本契约。"""

    def test_radius_merges_close_and_keeps_far(self):
        # 灰 100/140 的 LAB 距约 16.1 ≤ 27（合并）；灰 20/220 距约 81.5 > 27（分开）。
        entries = [
            ((100, 100, 100), _sample(index=0)),
            ((140, 140, 140), _sample(index=1)),
            ((20, 20, 20), _sample(index=2)),
            ((220, 220, 220), _sample(index=3)),
        ]
        clusters = cluster_font_colors(entries, radius=CLUSTER_RADIUS)
        self.assertEqual(sorted(c.count for c in clusters), [1, 1, 2])
        self.assertEqual(len(clusters), 3)

    def test_radius_is_a_lab_threshold(self):
        # 同一对颜色：半径 10 分两簇，半径 20 合一簇（距 16.1）。
        entries = [
            ((100, 100, 100), _sample(index=0)),
            ((140, 140, 140), _sample(index=1)),
        ]
        self.assertEqual(len(cluster_font_colors(entries, radius=10)), 2)
        self.assertEqual(len(cluster_font_colors(entries, radius=20)), 1)

    def test_representative_is_mode_not_mean(self):
        # A 出现 3 次、B 出现 1 次且同在半径内；B 放输入最前也不许当代表。
        a, b = (200, 30, 30), (210, 40, 40)  # 均值 [205,35,35] 不是任何块的真实色
        entries = [
            (b, _sample(index=0)),
            (a, _sample(index=1)),
            (a, _sample(index=2)),
            (a, _sample(index=3)),
        ]
        clusters = cluster_font_colors(entries)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].rgb, [200, 30, 30])
        self.assertEqual(clusters[0].count, 4)  # 块数，不是不同色数（2）

    def test_samples_sorted_then_capped(self):
        entries = [
            ((10, 10, 10), _sample("p2", 3)),
            ((10, 10, 10), _sample("p1", 7)),
            ((10, 10, 10), _sample("p1", 2)),
            ((10, 10, 10), _sample("p3", 0)),
            ((10, 10, 10), _sample("p1", 5)),
            ((10, 10, 10), _sample("p2", 1)),
        ]
        ordered = [("p1", 2), ("p1", 5), ("p1", 7), ("p2", 1), ("p2", 3), ("p3", 0)]
        got = cluster_font_colors(entries)[0].samples
        self.assertEqual([(s.pagename, s.block_index) for s in got], ordered[:4])
        got2 = cluster_font_colors(entries, max_samples=2)[0].samples
        self.assertEqual([(s.pagename, s.block_index) for s in got2], ordered[:2])

    def test_invalid_rgb_rejected(self):
        # normalize_rgb 是唯一有效性闸门：越界/长度错/非数值/None 一律丢弃。
        entries = [
            ([999, 0, 0], _sample(index=0)),
            ([1, 2], _sample(index=1)),
            (None, _sample(index=2)),
            ("abc", _sample(index=3)),
            ([], _sample(index=4)),
            ([12, 34, 56], _sample(index=5)),
        ]
        clusters = cluster_font_colors(entries)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].rgb, [12, 34, 56])
        self.assertEqual(clusters[0].count, 1)

    def test_float_array_colors_accepted_and_truncated(self):
        # TextBlock.fg_colors 可能是 float 数组：按 int() 截断后参与比较。
        entries = [
            (np.array([200.9, 30.2, 30.7], np.float32), _sample(index=0)),
            (np.array([200.0, 31.0, 30.0], np.float64), _sample(index=1)),
        ]
        clusters = cluster_font_colors(entries)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].rgb, [200, 30, 30])
        self.assertEqual(clusters[0].count, 2)

    def test_deterministic_and_order_independent(self):
        entries = [
            ((200, 30, 30), _sample("p1", 0)),
            ((210, 40, 40), _sample("p1", 1)),
            ((200, 30, 30), _sample("p2", 0)),
            ((210, 40, 40), _sample("p2", 1)),
            ((20, 20, 20), _sample("p1", 2)),
        ]
        first = cluster_font_colors(entries)
        self.assertEqual(first, cluster_font_colors(list(entries)))
        self.assertEqual(first, cluster_font_colors(list(reversed(entries))))


class ScanProjectColorsTest(unittest.TestCase):
    """``scan_project_colors`` 在小型假项目上的计数与样本定位。"""

    @staticmethod
    def _blk(fg, xyxy):
        return SimpleNamespace(fg_colors=fg, xyxy=xyxy)

    def test_counters_and_samples(self):
        proj = SimpleNamespace(
            pages={
                "p1": [
                    self._blk([200, 30, 30], [0, 0, 10, 10]),
                    self._blk([200, 30, 30], [10, 0, 20, 10]),
                    self._blk([200, 30, 30], [5, 5, 5, 5]),  # 退化框，无法定位
                ],
                "p2": [
                    self._blk(None, [0, 0, 10, 10]),  # 无颜色
                    self._blk([300, 0, 0], [0, 0, 10, 10]),  # 非法 rgb
                ],
            }
        )
        res = scan_project_colors(proj)
        self.assertEqual(res["total_blocks"], 5)
        self.assertEqual(res["colored_blocks"], 2)
        self.assertEqual(res["uncolored_blocks"], 3)
        self.assertEqual(len(res["clusters"]), 1)
        cluster = res["clusters"][0]
        self.assertEqual(cluster.rgb, [200, 30, 30])
        self.assertEqual(cluster.count, 2)
        self.assertEqual(
            [(s.pagename, s.block_index) for s in cluster.samples],
            [("p1", 0), ("p1", 1)],
        )
        self.assertEqual(cluster.samples[0].rect, [0, 0, 10, 10])


if __name__ == "__main__":
    unittest.main()
