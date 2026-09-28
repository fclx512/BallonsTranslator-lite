"""备注问号弹层演示动画生成器（离屏渲染真实控件 → 无损动画 WebP）。

用法：
    ./ballontrans_pylibs_win/python.exe scripts/gen_help_anim.py --scene punctuation_position
    可选：--out 路径 / --fps 帧率 / --theme 主题名（默认取当前配置主题）
         --dump-frames 目录（逐帧导出 PNG，供目视检查）

管线要点：
- QT_QPA_PLATFORM=offscreen：不弹窗口、不依赖 GPU；必须在 QApplication 之前设置。
- 帧号确定性步进：所有动画状态由帧号推算（set_state），不依赖真实时钟，
  生成结果可复现；界面改版后重跑本脚本即再生成。
- 样式复用 config/stylesheet.css + 用户主题（ui.misc.parse_stylesheet），
  下拉框用真实 ConfigComboBox，保证与设置页观感一致。
- 编码：Pillow 无损动画 WebP（lossless + method=6）；纯色 UI 内容下体积
  最小，且 PyQt6 自带 qwebp 插件，QMovie 可直接播放。
- 竖排标点的两种摆放按 ui/text_engine/vertical_layout.py::updateDrawOffsets
  的规则绘制：居中 = 字符框正中；靠边 = 墨迹贴字符框右上角。

动画内文字为烘焙像素、不走 i18n（演示面向中文用户，直书中文）。
"""

import argparse
import math
import os
import os.path as osp
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# offscreen 平台用基础字体库、不读系统字体注册表，须显式指定字体目录
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
from PIL import Image
from qtpy.QtCore import QPointF, QRectF, Qt
from qtpy.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen
from qtpy.QtWidgets import QApplication, QLabel, QWidget

from ui.custom_widget import ConfigComboBox
from ui.misc import get_theme_color, parse_stylesheet
from utils.config import load_config
from utils.shared import CONFIG_COMBOBOX_HEIGHT

# ── 时间轴（帧号，@10fps → 3.3s）────────────────────────────────
F_CURSOR_ARRIVE = 6   # 光标移动到下拉框
F_PRESS = 7           # 按下下拉框
F_OPEN = 8            # 下拉列表展开（两帧展开动画）
F_CLICK = 14          # 点选「靠边」
F_MOVE_START = 15     # 标点开始移动
F_MOVE_END = 24       # 标点到位
F_CURSOR_FADE = 26    # 光标开始淡出（4 帧）
N_FRAMES = 33

OPT_CENTER = "居中（繁体中文/日文）"
OPT_EDGE = "靠边（简体中文）"

CAPTIONS = (
    "<b>居中</b>：、。位于字符框正中——繁体中文/日文惯例",
    "切换到<b>靠边</b>：句读点移向字符框右上角",
    "<b>靠边</b>：、。停在右上角——简体中文惯例",
)

# 竖排预览文本：两列，日文竖排从右往左；句读点用 (列, 序) 标记
COLUMNS = ("今日は、", "晴れです。")
PUNCT_CELLS = {(0, 3), (1, 4)}
CELL = 30
COL_GAP = 18
INSET = 1.0


def clamp01(v):
    return max(0.0, min(1.0, v))


def out_cubic(t):
    t = clamp01(t)
    return 1.0 - (1.0 - t) ** 3


class PunctuationScene(QWidget):
    """460x262 的单场景画布：设置行（真实控件）+ 竖排预览（自绘）。"""

    SIZE = (460, 262)
    ROW_Y = 18
    PANEL = (16, 58, 170, 182)  # x, y, w, h（竖排预览卡，模拟漫画页面）

    def __init__(self, parent=None):
        super().__init__(parent)
        w, h = self.SIZE
        self.setFixedSize(w, h)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.fg = get_theme_color(key="@qwidgetForegroundColor")
        self.accent = get_theme_color(key="@accentPrimary")
        self.border = get_theme_color(key="@borderColor")
        self.card = get_theme_color(key="@qwidgetBackgroundColor")

        row_h = CONFIG_COMBOBOX_HEIGHT
        combo_w = 250
        self.combo = ConfigComboBox(options=[OPT_CENTER, OPT_EDGE])
        self.combo.setParent(self)
        self.combo.setFixedWidth(combo_w)
        self.combo.setGeometry(124, self.ROW_Y, combo_w, row_h)
        self.combo.setCurrentIndex(0)

        label = QLabel("标点位置", self)
        label.setStyleSheet(
            f"color: rgb({self.fg.red()},{self.fg.green()},{self.fg.blue()});"
            "background: transparent; font-size: 12px;"
        )
        label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        label.setGeometry(16, self.ROW_Y, 100, row_h)

        self.caption = QLabel(self)
        caption_fg = QColor(self.fg)
        caption_fg.setAlpha(210)
        self.caption.setStyleSheet(
            f"color: rgba({caption_fg.red()},{caption_fg.green()},"
            f"{caption_fg.blue()},{caption_fg.alpha()});"
            "background: transparent; font-size: 12px;"
        )
        self.caption.setWordWrap(True)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.caption.setGeometry(
            self.PANEL[0] + self.PANEL[2] + 16, self.PANEL[1] + 14, 244, 150
        )

        self._cursor = QPointF(w - 30, h - 26)
        self._cursor_alpha = 1.0
        self._cursor_press = False
        self._open_t = 0.0
        self._hover = -1
        self._ring_k = 0.0
        self._move_t = 0.0
        self._guide_a = 0.0

        # 光标/下拉列表画在置顶的透明覆盖层上，保证盖过真实子控件；
        # 覆盖层是裸 QWidget，必须显式抵消全局 QSS 的 QWidget 底色规则
        self._overlay = QWidget(self)
        self._overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._overlay.setStyleSheet("background: transparent;")
        self._overlay.setGeometry(0, 0, w, h)
        self._overlay.paintEvent = self._paint_overlay

    # ── 状态推进 ────────────────────────────────────────────────
    def set_state(self, f: int):
        idx = 1 if f >= F_CLICK else 0
        if self.combo.currentIndex() != idx:
            self.combo.setCurrentIndex(idx)

        if F_OPEN <= f < F_CLICK:
            self._open_t = out_cubic((f - F_OPEN + 1) / 2.0)
        else:
            self._open_t = 0.0

        self._hover = 1 if F_OPEN + 3 <= f < F_CLICK else -1

        # 光标轨迹：入场 → 下拉框 → 菜单项，随后原地淡出
        target = self._combo_click_point()
        item_pt = self._item_point(1)
        if f <= F_CURSOR_ARRIVE:
            t = out_cubic(f / F_CURSOR_ARRIVE)
            start = QPointF(self.width() - 30, self.height() - 26)
            self._cursor = start + (target - start) * t
        elif f < F_OPEN:
            self._cursor = target
        else:
            t = out_cubic(clamp01((f - F_OPEN) / 4.0))
            self._cursor = target + (item_pt - target) * t
        self._cursor_alpha = (
            1.0 if f <= F_CURSOR_FADE else 1.0 - clamp01((f - F_CURSOR_FADE) / 4.0)
        )
        self._cursor_press = f in (F_PRESS, F_CLICK)

        if F_PRESS <= f <= F_PRESS + 1:
            self._ring_k = (f - F_PRESS + 1) / 2.0
        elif F_CLICK <= f <= F_CLICK + 1:
            self._ring_k = (f - F_CLICK + 1) / 2.0
        else:
            self._ring_k = 0.0

        steps = F_MOVE_END - F_MOVE_START + 1
        self._move_t = out_cubic((f - F_MOVE_START + 1) / steps)
        guide_in = clamp01((f - F_MOVE_START + 2) / 3.0)
        guide_out = 1.0 - clamp01((f - F_MOVE_END) / 3.0)
        self._guide_a = min(guide_in, guide_out)

        cap = 0 if f < F_CLICK else (1 if f < F_MOVE_END + 2 else 2)
        if self.caption.text() != CAPTIONS[cap]:
            self.caption.setText(CAPTIONS[cap])

    def _combo_click_point(self) -> QPointF:
        return QPointF(
            self.combo.x() + self.combo.width() - 40,
            self.combo.y() + self.combo.height() / 2,
        )

    def _item_point(self, i: int) -> QPointF:
        r = self._dropdown_rect()
        return QPointF(r.x() + r.width() / 2, r.y() + 8 + 30 * i + 15)

    def _dropdown_rect(self) -> QRectF:
        c = self.combo
        return QRectF(c.x(), c.y() + c.height() + 2, c.width(), 8 + 30 * 2)

    # ── 绘制 ────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_preview(p)

    def _paint_preview(self, p: QPainter):
        px, py, pw, ph = self.PANEL
        path_rect = QRectF(px, py, pw, ph)
        p.setPen(QPen(self.border, 1))
        p.setBrush(QColor(255, 255, 255))
        p.drawRoundedRect(path_rect, 6, 6)

        font = QFont("Microsoft YaHei")
        font.setPixelSize(24)
        p.setFont(font)
        fm = QFontMetricsF(font)

        ink_color = QColor(26, 26, 26)
        col_w = CELL
        total_w = col_w * len(COLUMNS) + COL_GAP * (len(COLUMNS) - 1)
        x0 = px + (pw - total_w) / 2  # 第一列在最右（竖排从右往左）
        y0 = py + 16

        w_tint = math.sin(math.pi * clamp01(self._move_t))
        for ci, text in enumerate(COLUMNS):
            col_cx = x0 + total_w - col_w / 2 - ci * (col_w + COL_GAP)
            for i, ch in enumerate(text):
                cell_top = y0 + i * CELL
                cell_cy = cell_top + CELL / 2
                ink = fm.tightBoundingRect(ch)
                is_punct = (ci, i) in PUNCT_CELLS
                if is_punct:
                    self._draw_cell_guide(
                        p, QRectF(col_cx - CELL / 2, cell_top, CELL, CELL)
                    )
                    cx = col_cx - ink.width() / 2 - ink.left()
                    cy = cell_cy - ink.height() / 2 - ink.top()
                    ex = (col_cx + CELL / 2 - INSET) - ink.width() - ink.left()
                    ey = (cell_top + INSET) - ink.top()
                    x = cx + (ex - cx) * self._move_t
                    y = cy + (ey - cy) * self._move_t
                    k = w_tint
                else:
                    x = col_cx - ink.width() / 2 - ink.left()
                    y = cell_cy - ink.height() / 2 - ink.top()
                    k = 0.0
                color = QColor(
                    int(ink_color.red() + (self.accent.red() - ink_color.red()) * k),
                    int(ink_color.green() + (self.accent.green() - ink_color.green()) * k),
                    int(ink_color.blue() + (self.accent.blue() - ink_color.blue()) * k),
                )
                p.setPen(color)
                # x/y 已是基线坐标（目标墨迹左上角 - tightBoundingRect 偏移）
                p.drawText(QPointF(x, y), ch)

    def _draw_cell_guide(self, p: QPainter, rect: QRectF):
        if self._guide_a <= 0.01:
            return
        c = QColor(self.accent)
        c.setAlpha(int(200 * self._guide_a))
        pen = QPen(c, 1, Qt.PenStyle.DashLine)
        pen.setDashPattern([3, 2])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)

    def _paint_overlay(self, event):
        p = QPainter(self._overlay)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_dropdown(p)
        if self._ring_k > 0:
            self._paint_ring(p)
        if self._cursor_alpha > 0.01:
            self._paint_cursor(p)

    def _paint_dropdown(self, p: QPainter):
        if self._open_t <= 0.01:
            return
        full = self._dropdown_rect()
        h = full.height() * self._open_t
        rect = QRectF(full.x(), full.y(), full.width(), h)
        p.setPen(QPen(self.border, 1))
        p.setBrush(self.card)
        p.drawRoundedRect(rect, 6, 6)

        p.setClipRect(rect)
        for i, text in enumerate((OPT_CENTER, OPT_EDGE)):
            item = QRectF(full.x() + 4, full.y() + 8 + 30 * i, full.width() - 8, 30)
            if i == self._hover:
                hover = QColor(self.accent)
                hover.setAlpha(40)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(hover)
                p.drawRoundedRect(item, 4, 4)
            fg = QColor(self.fg)
            if i == self.combo.currentIndex():
                p.setPen(self.accent)
                p.drawText(
                    item.adjusted(0, 0, -12, 0)
                    .translated(0, 0),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    "✓",
                )
            else:
                p.setPen(fg)
            p.setFont(self.font())
            p.drawText(
                item.adjusted(10, 0, -24, 0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )

    def _paint_ring(self, p: QPainter):
        k = self._ring_k
        c = QColor(self.accent)
        c.setAlpha(int(220 * (1.0 - k)))
        pt = self._combo_click_point()
        p.setPen(QPen(c, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        r = 10 + 8 * k
        p.drawEllipse(pt, r, r)

    def _paint_cursor(self, p: QPainter):
        scale = 1.4 * (0.85 if self._cursor_press else 1.0)
        pts = [
            (0, 0), (0, 14.5), (3.1, 11.8), (5.6, 17.2), (8.0, 16.2),
            (5.6, 10.6), (10.2, 10.6),
        ]
        path = QPainterPath()
        path.moveTo(self._cursor)
        for x, y in pts[1:]:
            path.lineTo(self._cursor + QPointF(x * scale, y * scale))
        path.closeSubpath()
        c = QColor(self.fg)
        c.setAlpha(int(255 * self._cursor_alpha))
        p.setPen(QPen(c, 1.2))
        p.setBrush(QColor(255, 255, 255, int(255 * self._cursor_alpha)))
        p.drawPath(path)


SCENES = {"punctuation_position": PunctuationScene}
DEFAULT_OUT_DIR = osp.join(ROOT, "config", "help_anims")


def qimage_to_pil(img: QImage) -> Image.Image:
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    n = img.sizeInBytes()
    ptr = img.constBits()
    ptr.setsize(n)
    arr = np.frombuffer(ptr, dtype=np.uint8, count=n)
    arr = arr.reshape(h, bpl)[:, : w * 4].reshape(h, w, 4)
    return Image.fromarray(arr, "RGBA")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scene", default="punctuation_position", choices=sorted(SCENES))
    ap.add_argument("--out", default=None, help="输出 .webp 路径")
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--theme", default="", help="主题名，默认取当前配置")
    ap.add_argument("--dump-frames", dest="dump_frames", default=None)
    args = ap.parse_args()

    load_config()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(parse_stylesheet(args.theme))

    scene = SCENES[args.scene]()
    scene.show()
    app.processEvents()

    frames = []
    for f in range(N_FRAMES):
        scene.set_state(f)
        app.processEvents()
        frames.append(qimage_to_pil(scene.grab().toImage()))
        if args.dump_frames:
            os.makedirs(args.dump_frames, exist_ok=True)
            frames[-1].save(osp.join(args.dump_frames, f"frame_{f:03d}.png"))

    out = args.out or osp.join(DEFAULT_OUT_DIR, f"{args.scene}.webp")
    os.makedirs(osp.dirname(out), exist_ok=True)
    frames[0].save(
        out,
        "WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=int(1000 / args.fps),
        loop=0,
        lossless=True,
        method=6,
        exact=True,
    )
    size_kb = osp.getsize(out) / 1024
    print(f"OK {out}  {N_FRAMES} frames @ {args.fps}fps  {size_kb:.1f} KB")


if __name__ == "__main__":
    main()
