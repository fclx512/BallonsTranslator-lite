"""Offscreen tests: 分节卡的描边与卡片间隙（2026-09-20 实机反馈的两条回归）。

症状：卡片描边与圆角被卡内容器整段盖住、相邻卡片之间看不出分界。
真因：页内排版容器（裸 ``QWidget``、``ConfigSubBlock``、``QLabel``）落回全局
``QWidget { background-color }`` 规则，而它的值（``@widgetBackgroundColor``）
正是卡片的底色——同色时肉眼看不见，盖住的却是子控件后画、且不随父级圆角裁剪
的那层描边。修法见 ``ui/configpanel.py::ConfigFlatContainer`` 与 stylesheet
里同名的 QSS 规则。

测试自己注入高对比 token（页面凹面=品红、卡片=绿、描边=白），使"谁盖了谁"
与用户主题无关（同 ``scripts/settings_render.py --diag`` 的思路）。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_config_card_painting.py
"""

import os
import os.path as osp
import sys
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qtpy.QtCore import QPoint  # noqa: E402
from qtpy.QtWidgets import QApplication, QWidget  # noqa: E402

# 三层色调 + 描边，互不相同才量得出来
PAGE_HEX = "#ff00ff"
CARD_HEX = "#00b400"
EDGE_HEX = "#ffffff"
PROBE_TOKENS = {
    "@emptyContentBackgroundColor": PAGE_HEX,
    "@widgetBackgroundColor": CARD_HEX,
    "@borderColor": EDGE_HEX,
}
# 卡片描边是 1px 线，抓图后允许被抗锯齿混色——按通道差容忍
EDGE_PIXEL = (255, 255, 255)


def _rgb(hex_color: str):
    return tuple(int(hex_color[i : i + 2], 16) for i in (1, 3, 5))


def _close(px, ref, tol=70):
    return all(abs(a - b) <= tol for a, b in zip(px, ref))


class CardPaintingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.configpanel import ConfigPanel

        cls.app = QApplication.instance() or QApplication([])
        cls.panel = ConfigPanel()
        cls.panel.resize(1000, 700)
        cls.panel.show()
        cls.app.processEvents()

        from ui.misc import _resolve_theme, build_stylesheet_from_dict
        from utils.config import pcfg

        theme = dict(_resolve_theme(pcfg.dark_theme))
        theme.update(PROBE_TOKENS)
        cls._old_qss = cls.app.styleSheet()
        cls.app.setStyleSheet(build_stylesheet_from_dict(theme))
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.app.setStyleSheet(cls._old_qss)
        cls.panel.hide()
        cls.panel.deleteLater()
        cls.app.processEvents()

    # ── helpers ───────────────────────────────────────────────────────

    def _page(self, section_key: str):
        """切到该页并返回页面体（可整页 grab，不必滚到可见）。"""
        self.panel.configNav.section_items[section_key].click()
        self.app.processEvents()
        area = self.panel.pageStack.currentWidget()
        return area.widget() if hasattr(area, "widget") else area

    def _cards(self, page):
        from ui.custom_widget import PanelGroupBox

        return [
            c
            for c in page.findChildren(PanelGroupBox)
            if c.property("compact") and c.isVisible()
        ]

    def _grab(self, page):
        img = page.grab().toImage()
        return img, (img.devicePixelRatio() or 1.0)

    # ── 描边：四边都得看得见 ───────────────────────────────────────────

    def test_card_border_visible_on_all_edges(self):
        page = self._page("typesetting")
        img, dpr = self._grab(page)
        checked = 0
        for card in self._cards(page):
            tl = card.mapTo(page, QPoint(0, 0))
            x0, y0 = int(tl.x() * dpr), int(tl.y() * dpr)
            x1, y1 = int((tl.x() + card.width()) * dpr), int(
                (tl.y() + card.height()) * dpr
            )
            mid_x, mid_y = (x0 + x1) // 2, (y0 + y1) // 2

            def has_edge(xs, ys):
                return any(
                    _close(img.pixelColor(x, y).getRgb()[:3], EDGE_PIXEL, 90)
                    for x in xs
                    for y in ys
                )

            for name, found in (
                ("左", has_edge(range(max(0, x0 - 2), x0 + 5), [mid_y])),
                ("右", has_edge(range(x1 - 4, x1 + 3), [mid_y])),
                ("上", has_edge([mid_x], range(max(0, y0 - 2), y0 + 5))),
                ("下", has_edge([mid_x], range(y1 - 4, y1 + 3))),
            ):
                self.assertTrue(
                    found,
                    f"卡片「{card.title_label.text()}」的{name}边看不见描边"
                    f"（被卡内容器的不透明底色盖住了）",
                )
            checked += 1
        self.assertGreaterEqual(checked, 5, "嵌字页应有多张分节卡")

    # ── 竖向对齐：各页卡片落在同一条竖线上 ─────────────────────────────

    def test_cards_share_one_vertical_line(self):
        """管线标签页的卡片左缘必须与其它页对齐。

        阶段面板的父布局若吃 Qt 样式默认边距（实测 9px），卡片就比页框容器的
        8px 右移 1px——同一个坑 2026-09-20 在 `ConfigSubBlock` 上踩过。
        """
        def left_edge(section_key):
            page = self._page(section_key)
            cards = self._cards(page)
            self.assertTrue(cards, f"{section_key} 页没有分节卡")
            return min(c.mapTo(self.panel, QPoint(0, 0)).x() for c in cards)

        reference = left_edge("models")
        for key in ("pipeline", "typesetting", "config_mgmt"):
            self.assertEqual(
                left_edge(key), reference,
                f"{key} 页的卡片左缘与模型管理页不在同一条竖线上",
            )

    # ── 间隙：卡片之间露出页面凹面 ─────────────────────────────────────

    def test_pipeline_stage_panel_paints_no_background(self):
        """管线阶段面板（裸 ``QWidget``）不得画底色，否则「模块选择行 → 参数卡」
        之间的凹陷槽被填平，页体凹陷白做。

        这条今天成立是因为面板是裸 QWidget（QSS 的全局 QWidget 规则不落到它身上）；
        一旦有人把它换成 ``ui/custom_widget/widget.py::Widget``（WA_StyledBackground）
        就会真的刷上底色——本用例是那一步的绊线。
        """
        page = self._page("pipeline")
        img, dpr = self._grab(page)
        cards = self._cards(page)
        self.assertTrue(cards, "管线页应有一张参数卡（当前标签）")
        card = cards[0]
        tl = card.mapTo(page, QPoint(0, 0))
        # 卡片正上方 4/8px：模块选择行的右侧空档，那里只有阶段面板自己
        x = int((tl.x() + card.width() * 0.75) * dpr)
        for dy in (4, 8):
            p = img.pixelColor(x, int((tl.y() - dy) * dpr)).getRgb()[:3]
            self.assertTrue(
                _close(p, _rgb(PAGE_HEX)),
                f"参数卡上方 {dy}px 处的底色是 {p}，不是页面凹面"
                f"（阶段面板自己画了底色，见 ui/module_parse_widgets.py）",
            )

    def test_gap_between_cards_shows_page_surface(self):
        for section_key in ("typesetting", "project", "interface"):
            page = self._page(section_key)
            img, dpr = self._grab(page)
            cards = self._cards(page)
            self.assertGreater(len(cards), 1, f"{section_key} 页应有相邻卡片")
            for a, b in zip(cards, cards[1:]):
                ta = a.mapTo(page, QPoint(0, 0))
                tb = b.mapTo(page, QPoint(0, 0))
                gx = int((ta.x() + a.width() // 2) * dpr)
                gy = int(
                    ((ta.y() + a.height() + tb.y()) / 2) * dpr
                )
                p = img.pixelColor(gx, gy).getRgb()[:3]
                self.assertTrue(
                    _close(p, _rgb(PAGE_HEX)),
                    f"{section_key}：「{a.title_label.text()}」与"
                    f"「{b.title_label.text()}」之间的底色是 {p}，"
                    f"不是页面凹面（卡片之间的分界被同色填充抹平了）",
                )

    # ── 守卫：整条描边都得在（不只是中点那一处） ──────────────────────

    def _missing_edge_positions(self, img, dpr, card, page):
        """逐行/逐列检查卡片四边，返回找不到描边的位置。

        整条边都要查：只采中点会漏掉"局部盖住"——例如模型文件卡底部的状态条
        只盖住卡片左右描边的 28px，中点照样是好的。
        """
        tl = card.mapTo(page, QPoint(0, 0))
        x0, y0 = int(tl.x() * dpr), int(tl.y() * dpr)
        x1, y1 = int((tl.x() + card.width()) * dpr), int((tl.y() + card.height()) * dpr)
        # 圆角区里描边会内收，跳过两端（半径 7px 卡片边长 ≤1 时整体跳过）
        r = int(9 * dpr)
        if x1 - x0 < 4 * r or y1 - y0 < 4 * r:
            return []
        win = int(10 * dpr)  # 描边可能被抗锯齿推到相邻像素上

        def has(xs, ys):
            return any(
                _close(img.pixelColor(x, y).getRgb()[:3], EDGE_PIXEL, 90)
                for x in xs
                for y in ys
            )

        missing = []
        for y in range(y0 + r, y1 - r):
            if not has(range(x0 - 2, x0 + win), [y]):
                missing.append(f"左@y={y - y0}")
            if not has(range(x1 - win, x1 + 3), [y]):
                missing.append(f"右@y={y - y0}")
        for x in range(x0 + r, x1 - r):
            if not has([x], range(y0 - 2, y0 + win)):
                missing.append(f"上@x={x - x0}")
            if not has([x], range(y1 - win, y1 + 3)):
                missing.append(f"下@x={x - x0}")
        return missing

    def test_every_card_border_is_continuous(self):
        """嵌字/项目/界面/模型/快捷键/应用/工作台/管线 八页的分节卡：四边描边不得断。"""
        for key in (
            "models",
            "pipeline",
            "project",
            "typesetting",
            "interface",
            "shortcuts",
            "config_mgmt",
            "workbench_temp",
        ):
            page = self._page(key)
            img, dpr = self._grab(page)
            for card in self._cards(page):
                if card.width() < 60 or card.height() < 40:
                    continue
                missing = self._missing_edge_positions(img, dpr, card, page)
                self.assertEqual(
                    missing[:4],
                    [],
                    f"{key}：「{card.title_label.text()}」卡的描边有 "
                    f"{len(missing)} 处缺失（{missing[:4]}）——卡内有控件用不透明"
                    f"底色盖住了描边",
                )


if __name__ == "__main__":
    unittest.main()
