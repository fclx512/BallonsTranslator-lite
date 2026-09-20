"""OpenBtn（左栏打开菜单钮）视觉契约：常态 / hover / 菜单展开三态。

它**不是开关**，hover 不能长得跟左栏其余开关的激活态一样（强调色染底 +
activate 图标）——用户 2026-09-20 报告的就是这一点：点开菜单再收起后，
图标看着还像「已启用」。契约（config/stylesheet.css 的 OpenBtn 规则）：

    rest      无染底、无描边、常态图标
    hover     只描 1px 圆角边（@borderColor）、**不填充**、**不换图标**
    展开态    强调色染底 + activate 图标（清属性后必须回到 rest）

测法是离屏 grab 后数像素：底色取自按钮外面的容器像素、描边色取自主题
`@borderColor`，都不硬编码色值（换主题/自定义主题同样成立）；图标只断言
「同一位置的像素数不变 / 颜色变了」，不钉具体色值。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_openbtn_states.py -v
"""

import os
import sys
import unittest
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtCore import Qt  # noqa: E402
from qtpy.QtWidgets import QApplication, QVBoxLayout  # noqa: E402

from ui.mainwindowbars import OpenBtn  # noqa: E402
from ui.misc import get_theme_color, parse_stylesheet  # noqa: E402
from utils.config import pcfg  # noqa: E402

BTN_SIZE = 33


class OpenBtnStatesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(
            sys.argv[:1] + ["--platform", "offscreen"]
        )

    def setUp(self):
        self.app.processEvents()
        # QSS 里的 url(icons/...) 相对**当前工作目录**解析
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(REPO_ROOT)
        self.addCleanup(self.app.setStyleSheet, self.app.styleSheet())

        theme = pcfg.dark_theme if pcfg.darkmode else pcfg.light_theme
        self.app.setStyleSheet(parse_stylesheet(theme, False))
        self.border = get_theme_color(key="@borderColor").name()

        # 按钮挂在 Widget 上：容器底色 = @widgetBackgroundColor，与按钮常态
        # 底色同色——所以「有没有染底」只能靠与容器像素比对
        from ui.custom_widget import Widget

        self.bar = Widget()
        self.bar.setFixedWidth(48)
        layout = QVBoxLayout(self.bar)
        layout.setContentsMargins(4, 10, 4, 10)
        self.btn = OpenBtn()
        self.btn.setFixedSize(BTN_SIZE, BTN_SIZE)
        layout.addWidget(self.btn)
        self.bar.resize(48, 60)
        self.bar.show()
        self.app.processEvents()
        self.bar_bg = self._container_bg()

    def tearDown(self):
        self.bar.hide()
        self.bar.deleteLater()
        self.app.processEvents()

    # ── helpers ────────────────────────────────────────────────

    def _container_bg(self) -> str:
        """按钮外面一点的容器像素（常态底色基准）。"""
        img = self.bar.grab().toImage()
        return img.pixelColor(2, 2).name()

    def _snapshot(self) -> Counter:
        img = self.bar.grab().toImage()
        window = img.copy(self.btn.x(), self.btn.y(), BTN_SIZE, BTN_SIZE)
        return Counter(
            window.pixelColor(x, y).name()
            for y in range(BTN_SIZE)
            for x in range(BTN_SIZE)
        )

    def _repolish(self) -> None:
        self.btn.style().unpolish(self.btn)
        self.btn.style().polish(self.btn)
        self.btn.update()
        self.app.processEvents()

    def _set_hover(self, hovered: bool) -> None:
        self.btn.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, hovered)
        self._repolish()

    def _set_menu_open(self, open_: bool) -> None:
        self.btn.setProperty("menuOpen", open_)
        self._repolish()

    @staticmethod
    def _glyph(snap: Counter, fill: str) -> str:
        """去掉 fill 后出现最多的色 = 图标字形色。"""
        return Counter({c: n for c, n in snap.items() if c != fill}).most_common(1)[0][0]

    # ── 常态 ───────────────────────────────────────────────────

    def test_rest_has_no_fill_and_no_outline(self):
        snap = self._snapshot()
        self.assertEqual(snap.most_common(1)[0][0], self.bar_bg)
        self.assertEqual(snap.get(self.border, 0), 0, "常态不该有描边")
        self.assertTrue(self._glyph(snap, self.bar_bg), "常态要画出图标")

    # ── hover ──────────────────────────────────────────────────

    def test_hover_draws_outline_without_fill_or_icon_swap(self):
        rest = self._snapshot()
        rest_glyph = self._glyph(rest, self.bar_bg)
        rest_glyph_px = rest[rest_glyph]

        self._set_hover(True)
        hover = self._snapshot()

        # 描边出现，且**不填充**（中心仍是容器底色）
        self.assertGreater(
            hover.get(self.border, 0), 40,
            f"hover 应描一圈 @borderColor（{self.border}）",
        )
        self.assertEqual(hover.most_common(1)[0][0], self.bar_bg, "hover 不许染底")
        # 图标不换色也不缩放：同色像素数一模一样
        self.assertEqual(
            hover.get(rest_glyph, 0), rest_glyph_px,
            "hover 必须保持常态图标（换 activate 图标是开关激活态的语言）",
        )

        self._set_hover(False)
        self.assertEqual(self._snapshot(), rest, "移开光标要完全回到常态")

    # ── 菜单展开态 ─────────────────────────────────────────────

    def test_menu_open_fills_and_swaps_icon(self):
        rest = self._snapshot()
        rest_glyph = self._glyph(rest, self.bar_bg)

        self._set_menu_open(True)
        opened = self._snapshot()
        fill = opened.most_common(1)[0][0]

        self.assertNotEqual(fill, self.bar_bg, "菜单展开态要有染底")
        self.assertEqual(
            opened.get(self.border, 0), 0, "展开态的强调染底不叠描边",
        )
        open_glyph = self._glyph(opened, fill)
        self.assertNotEqual(open_glyph, rest_glyph, "展开态应切 activate 图标")
        # 图标只换色不该变形：字形像素量应与常态相当（留 5% 给抗锯齿差异）
        self.assertAlmostEqual(
            opened[open_glyph] / rest[rest_glyph], 1.0, delta=0.05,
            msg="展开态图标只应换色，不该缩放/移位",
        )

        # 展开态压过 hover（光标还停在按钮上时也必须显示「菜单开着」）
        self._set_hover(True)
        self.assertEqual(self._snapshot(), opened)

    def test_closing_menu_returns_to_rest(self):
        rest = self._snapshot()
        self._set_menu_open(True)
        self.assertNotEqual(self._snapshot(), rest)
        self._set_menu_open(False)
        self.assertEqual(
            self._snapshot(), rest, "收起菜单必须回到常态（清属性要 repolish）",
        )

    # ── 与主窗口的接线 ─────────────────────────────────────────

    def test_press_sets_and_clears_menu_open(self):
        """mousePressEvent 起落 menuOpen：菜单 exec 用替身，避免真弹窗。"""
        calls = []
        btn = self.btn

        class _FakeMenu:
            def exec(self, *args):
                calls.append(bool(btn.property("menuOpen")))
                return None

        self.btn.assignMenu(_FakeMenu())
        from qtpy.QtCore import QPointF
        from qtpy.QtGui import QMouseEvent

        press = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.btn.mousePressEvent(press)

        self.assertEqual(calls, [True], "菜单展开期间 menuOpen 必须是 True")
        self.assertFalse(bool(self.btn.property("menuOpen")), "收起后必须复位")


if __name__ == "__main__":
    unittest.main()
