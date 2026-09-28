"""备注问号弹层演示动画生成器（离屏渲染真实控件 → 无损动画 WebP）。

用法：
    ./ballontrans_pylibs_win/python.exe scripts/gen_help_anim.py --scene punctuation_position
    可选：--all（依次生成 SCENES 全部场景）/ --out 路径 / --out-dir 目录 /
         --fps 帧率 / --theme 主题名（默认取当前配置主题）/
         --platform windows|offscreen / --dump-frames 目录（逐帧导出 PNG，供目视检查）

管线要点：
- QT_QPA_PLATFORM 默认 windows（原生 DPR 就是真实屏幕值，**不设** QT_SCALE_FACTOR
  避免叠乘）；显式覆盖用环境变量或 ``--platform offscreen``（调试用）。windows
  平台下不 show 窗口也能直接 grab()，无闪窗。
- 产物分辨率 = 460×262 × 屏幕真实 DPR：windows 平台下 grab() 天然返回 DPR
  缩放后的物理像素；显示侧取屏幕 DPR 折算逻辑尺寸
  （ui/configpanel.py::ConfigNotePopup），生成 DPR = 显示 DPR 时 1 图像像素 =
  1 设备像素。产物物理尺寸绑定生成机的屏幕——跨机器不匹配时显示侧不插值、
  依然逐像素清晰，只是逻辑尺寸偏大/偏小，用设置页的重新生成按钮重出即可。
- 字体：预览字符与 UI 标签统一显式家族 "Microsoft YaHei UI"（GUI 的真实默认；
  offscreen 的"系统默认"会落到 Arial，中文走错回退链且只有灰度 AA，是产物
  发虚的根因），预览字符保持 DemiBold 字重提可读性；家族缺失时 Qt 自动回退。
- 帧号确定性步进：所有动画状态由帧号推算（set_state），不依赖真实时钟，
  生成结果可复现；界面改版后重跑本脚本即再生成。
- 样式复用 config/stylesheet.css + 用户主题（ui.misc.parse_stylesheet），
  下拉框用真实 ConfigComboBox，保证与设置页观感一致。
- 编码：Pillow 无损动画 WebP（lossless + method=6）；纯色 UI 内容下体积
  最小，且 PyQt6 自带 qwebp 插件，QMovie 可直接播放。
- 文案方案分两类：**行为对比类**（开关改变渲染行为，如标点布局/引号宽度/
  tcy/裁剪/块放大）用改前/改后两条**从头到尾同时显示**（改前在上、改后在
  下），未激活一条降到约 35% 不透明度、激活的全亮；点击后两条用 out_cubic
  约 5 帧交叉淡化互换强调状态，与预览过渡同步。**外观展示类**（开/关只是
  显示/不显示某个装饰，如序号/标签徽标）改单条常显说明，无前缀无互换。
  都用 rgba 前景色调 alpha，**不用** QGraphicsOpacityEffect、**没有**高亮
  边框。
- 竖排列的几何一律照 ui/text_engine/vertical_layout.py::layoutBlock /
  updateDrawOffsets 的数学：**进给/格高**用 QFontMetricsF.tightBoundingRect
  （引擎 get_punc_rect 用的就是它），**墨迹摆位**用字形真实轮廓
  （QPainterPath.addText，引擎画的就是向量轮廓，两者同一字形能差 ~1px）。
  改动这些场景后跑一次性探针与真机引擎逐字比对（见 docs 使用说明「验收」）。
- 每个场景自带 N_FRAMES（时长不同），main() 按场景类属性循环。
- 除标点场景外的 6 个场景都继承 _DemoScene：设置行（真实 ConfigCheckBox）
  + 预览卡 + 右侧两条常显文案，光标/点击涟漪/下拉画在置顶透明覆盖层上
  （构建完成后统一 raise_，保证盖过包括文案在内的所有子控件）。

动画内文字为烘焙像素、不走 i18n（演示面向中文用户，直书中文）。
"""

import argparse
import math
import os
import os.path as osp
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
# offscreen（仅显式覆盖时才走）平台用基础字体库、不读系统字体注册表，须显式
# 指定字体目录，否则中文全是豆腐块
if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
# 产物分辨率 = 460×262 × 屏幕真实 DPR（windows 平台下 grab 天然返回物理像素），
# 显示侧按屏幕 DPR 折算逻辑尺寸（ui/configpanel.py::ConfigNotePopup）。

ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
from PIL import Image
from qtpy.QtCore import QPointF, QRectF, Qt
from qtpy.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
)
from qtpy.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from ui.custom_widget import ConfigCheckBox, ConfigComboBox
from ui.misc import get_theme_color, parse_stylesheet
from utils.config import load_config
from utils.shared import CONFIG_COMBOBOX_HEIGHT, CONFIG_COMBOBOX_SHORT

# ── 标点场景的时间轴（帧号，@10fps → 2.5s）──────────────────────
# 其余场景的时间轴在各自类里（_DemoScene 的 F_* 与类属性 N_FRAMES）
F_CURSOR_ARRIVE = 6   # 光标移动到下拉框
F_PRESS = 7           # 按下下拉框
F_OPEN = 8            # 下拉列表展开（两帧展开动画）
F_CLICK = 12          # 点选「靠边」
F_MOVE_START = 13     # 标点开始移动 + 两条文案开始明暗互换
F_MOVE_END = 18       # 标点到位（互换也完成，共 6 帧）
F_CURSOR_FADE = 20    # 光标开始淡出（4 帧）

OPT_CENTER = "居中（繁体中文/日文）"
OPT_EDGE = "靠边（简体中文）"

CAPTIONS = (
    "<b>改前：</b>、。位于字符框正中（繁体中文/日文惯例）",
    "<b>改后：</b>、。停在字符框右上角（简体中文惯例）",
)

# ── 两条常显文案的版式（改前在上、改后在下，都在预览卡右侧）────────
CAPTION_X = 202
CAPTION_W = 244
CAPTION_H = 44
CAPTION_Y = (72, 122)

# 竖排预览文本：两列，日文竖排从右往左；句读点用 (列, 序) 标记
COLUMNS = ("今日は、", "晴れです。")
PUNCT_CELLS = {(0, 3), (1, 4)}
CELL = 30
COL_GAP = 18


def clamp01(v):
    return max(0.0, min(1.0, v))


def out_cubic(t):
    t = clamp01(t)
    return 1.0 - (1.0 - t) ** 3


# ── 画布取色/尺寸常量（照抄真实实现，来源写在各自注释里）────────────
TEXTRECT_SHOW_COLOR = QColor(30, 147, 229, 170)       # ui/textitem.py
TEXTRECT_SELECTED_COLOR = QColor(248, 64, 147, 170)    # ui/textitem.py
CLIP_WARN_COLOR = QColor(255, 200, 0, 200)             # ui/textitem.py 黄框
SEQ_BADGE_COLOR = QColor(0, 0, 0, 170)                 # 序号徽标底色
TAG_BADGE_DOUBT_COLOR = QColor(225, 88, 62, 220)       # 疑点类标签徽标
TAG_BADGE_DIRECTIVE_COLOR = QColor(72, 132, 240, 220)  # 指示类标签徽标
HANDLE_FILL_COLOR = QColor(200, 200, 200, 125)         # shape_control 手柄
HANDLE_BORDER_COLOR = QColor(75, 75, 75)
HANDLE_SIZE = 15.0    # CBEDGE_WIDTH(30) / 2：手柄实画边长
BADGE_H_PAD = 4       # 徽标内边距（引擎 _OrderBadgeItem / _TagBadgeItem）
BADGE_V_PAD = 2


# ── 字形墨迹的两把尺子（与引擎一致，别混用）──────────────────────
# 引擎：进给/格高来自 QFontMetricsF.tightBoundingRect（layout.py::get_punc_rect，
# 结果按整数取整）；墨迹摆位来自向量轮廓（rendering/glyph.py::glyph_geometry）。
# 本脚本照抄这两把尺子：格高用 tight，摆墨迹用轮廓——混用会让整体错 ~1px。
_INK_CACHE = {}


def outline_ink(font: QFont, ch: str) -> QRectF:
    """字形真实轮廓墨迹框（原点在基线，y 向下）。"""
    key = (font.family(), font.pixelSize(), font.weight(), ch)
    rect = _INK_CACHE.get(key)
    if rect is None:
        path = QPainterPath()
        path.addText(0.0, 0.0, font, ch)
        rect = path.boundingRect()
        _INK_CACHE[key] = rect
    return QRectF(rect)


def ink_pen(ink: QRectF, ink_left: float, ink_top: float) -> QPointF:
    """把墨迹左上角放到 (ink_left, ink_top) 时的 drawText 基线坐标。

    drawText 收的是基线坐标，须减去墨迹框偏移——**只减一次**（重复减会让整列
    字下坠错位，首个场景踩过）。
    """
    return QPointF(ink_left - ink.left(), ink_top - ink.top())


# ── 共用绘制小件（标点场景与本文件其余场景共用，观感语言一致）────────

def _cursor_path(point: QPointF, scale: float) -> QPainterPath:
    """鼠标箭头路径（原 PunctuationScene 的形状）。"""
    pts = (
        (0, 0), (0, 14.5), (3.1, 11.8), (5.6, 17.2), (8.0, 16.2),
        (5.6, 10.6), (10.2, 10.6),
    )
    path = QPainterPath()
    path.moveTo(point)
    for x, y in pts[1:]:
        path.lineTo(point + QPointF(x * scale, y * scale))
    path.closeSubpath()
    return path


def paint_cursor(painter: QPainter, point: QPointF, alpha: float,
                 pressed: bool, color: QColor):
    """白色箭头光标；按下时缩到 85%。"""
    if alpha <= 0.01:
        return
    c = QColor(color)
    c.setAlpha(int(255 * alpha))
    painter.setPen(QPen(c, 1.2))
    painter.setBrush(QColor(255, 255, 255, int(255 * alpha)))
    painter.drawPath(_cursor_path(point, 1.4 * (0.85 if pressed else 1.0)))


def paint_click_ring(painter: QPainter, point: QPointF, k: float, color: QColor):
    """点击涟漪，k 从 0 走到 1。"""
    if k <= 0.0:
        return
    c = QColor(color)
    c.setAlpha(int(220 * (1.0 - k)))
    painter.setPen(QPen(c, 2))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    r = 10 + 8 * k
    painter.drawEllipse(point, r, r)


# 显式家族：GUI 的真实默认。offscreen/裸默认的"系统默认"会落到 Arial，
# 中文走错回退链且只有灰度 AA，是产物发虚的根因；家族缺失时 Qt 自动回退。
UI_FONT_FAMILY = "Microsoft YaHei UI"


def preview_font(pixel_size: int) -> QFont:
    """预览字符字体：显式 YaHei UI 家族 + DemiBold 字重。

    用户点名的痛点：默认字重的黑体在小字号下渲染出来发虚，竖排预览字符
    统一提半档字重。家族显式钉 UI_FONT_FAMILY，不依赖平台默认解析。
    """
    font = QFont(UI_FONT_FAMILY)
    font.setPixelSize(pixel_size)
    font.setWeight(QFont.Weight.DemiBold)
    return font


def set_caption_emphasis(labels, k: float, color: QColor):
    """两条常显文案的明暗互换：未激活一条降到约 35% 不透明度，激活的全亮。

    k=0 改前条全亮（改后条暗），k=1 反转；点击复选框/下拉项后用 out_cubic
    约 5 帧交叉淡化互换，与预览过渡同步。实现是 QLabel.setStyleSheet 调
    rgba 前景色的 alpha，**禁止用 QGraphicsOpacityEffect**（图形效果接管
    重绘后在透明窗口上不可靠，见 ui/configpanel.py::ConfigNotePopup）。
    """
    hi = 210
    lo = round(hi * 0.35)
    rgb = f"{color.red()},{color.green()},{color.blue()}"
    alphas = (hi + (lo - hi) * k, lo + (hi - lo) * k)
    for label, alpha in zip(labels, alphas):
        label.setStyleSheet(
            f"color: rgba({rgb},{round(alpha)});"
            "background: transparent; font-size: 12px;"
        )


def make_caption_labels(parent: QWidget, fg: QColor, captions) -> list:
    """两条常显文案（改前在上、改后在下）；返回 labels。

    初始强调状态 = 改前条全亮（k=0）。
    """
    labels = []
    for index, text in enumerate(captions):
        label = QLabel(parent)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        label.setGeometry(CAPTION_X, CAPTION_Y[index], CAPTION_W, CAPTION_H)
        label.setText(text)
        labels.append(label)
    set_caption_emphasis(labels, 0.0, fg)
    return labels


def make_single_caption(parent: QWidget, fg: QColor, text: str) -> QLabel:
    """外观展示类场景的**单条常显说明**（无改前/改后前缀、无明暗互换）。

    适用判据：开关只是「显示/不显示」某个装饰（无布局行为差异）时，改前条
    「不显示 XX」没有信息量，改单条直述该功能呈现什么。行为对比类（开关改变
    渲染行为）仍走 ``make_caption_labels`` 的双条 + 明暗互换。
    """
    label = QLabel(parent)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setAlignment(
        Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
    )
    label.setGeometry(CAPTION_X, CAPTION_Y[0], CAPTION_W, CAPTION_H)
    rgb = f"{fg.red()},{fg.green()},{fg.blue()}"
    label.setStyleSheet(
        f"color: rgba({rgb},210);"
        "background: transparent; font-size: 12px;"
    )
    label.setText(text)
    return label


def draw_cell_guide(painter: QPainter, rect: QRectF, color: QColor, alpha: float):
    """虚线字格提示（标点场景的画法：强调「这个字占多大格」）。"""
    if alpha <= 0.01:
        return
    c = QColor(color)
    c.setAlpha(int(200 * alpha))
    pen = QPen(c, 1, Qt.PenStyle.DashLine)
    pen.setDashPattern([3, 2])
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(rect)


def draw_block_frame(painter: QPainter, rect: QRectF, color: QColor, width: float,
                     dashed: bool = False):
    """文本框描边（画布上的常规/选中描边与溢出黄框共用）。"""
    pen = QPen(color, width,
               Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(rect)


def draw_ink_center(painter: QPainter, fm: QFontMetricsF, ch: str, rect: QRectF,
                    color: QColor, ink: QRectF = None, font: QFont = None):
    """墨迹在 rect 里水平垂直居中（引擎对全宽字与直立西文的摆法）。

    引擎居中用的是**轮廓**墨迹（updateDrawOffsets 的 act_rect），给了 font 就
    按轮廓摆，否则退回 tightBoundingRect（徽标等小字示意用）。
    """
    if ink is None and font is not None:
        ink = outline_ink(font, ch)
    if ink is None:
        ink = fm.tightBoundingRect(ch)
    painter.setPen(color)
    painter.drawText(
        ink_pen(ink, rect.center().x() - ink.width() / 2,
                rect.center().y() - ink.height() / 2),
        ch,
    )


def draw_ink_top_right(painter: QPainter, fm: QFontMetricsF, ch: str, rect: QRectF,
                       color: QColor, ink: QRectF = None, font: QFont = None):
    """墨迹贴 rect 右上角（引擎 Simplified 靠边分支：墨迹顶格顶、右齐列宽）。

    引擎该分支是 xoff = -act_rect.left() + base_width - act_rect.width()、
    yoff = -act_rect.top()——即墨迹右边齐 base_width（= 列宽）、上边齐格顶，
    **没有**内缩余量。
    """
    if ink is None and font is not None:
        ink = outline_ink(font, ch)
    if ink is None:
        ink = fm.tightBoundingRect(ch)
    painter.setPen(color)
    painter.drawText(
        ink_pen(ink, rect.right() - ink.width(), rect.top()),
        ch,
    )


def draw_rotated_ink(painter: QPainter, ink: QRectF, ch: str, col_left: float,
                     col_w: float, cell_top: float, color: QColor, opening: bool,
                     shift: float = 0.0):
    """竖排里需旋转的字（「」『』）：顺时针转 90° 画在字格列里。

    摆位照 ui/text_engine/vertical_layout.py::updateDrawOffsets 的旋转分支：
    - 屏幕 y（沿列方向）= 格顶 + 墨迹左旁距；闭括号在 ALIGNL 分支整体减去该旁距
      （等价于墨迹贴格顶），开括号另有半角补偿 shift；
    - 屏幕 x：开括号贴列右缘（PUNSET_ROTATE_ALIGNR），闭括号贴列左缘
      （PUNSET_ROTATE_ALIGNL）。
    """
    if opening:
        tx = col_left + col_w + ink.top()      # 旋转后墨迹右缘齐列右缘
        ty = cell_top - shift
    else:
        tx = col_left + ink.bottom()           # 旋转后墨迹左缘齐列左缘
        ty = cell_top - ink.left()             # 等价于沿列贴格顶
    painter.save()
    try:
        painter.translate(tx, ty)
        painter.rotate(90)
        painter.setPen(color)
        painter.drawText(QPointF(0.0, 0.0), ch)
    finally:
        painter.restore()


class PunctuationScene(QWidget):
    """460x262 的单场景画布：设置行（真实控件）+ 竖排预览（自绘）。"""

    SIZE = (460, 262)
    N_FRAMES = 25
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

        self.caption_labels = make_caption_labels(self, self.fg, CAPTIONS)

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
        # 层级兜底：所有子控件建完后统一把覆盖层顶到最上层——
        # 下拉列表/光标要永远盖过包括文案 label 在内的一切子控件
        self._overlay.raise_()

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
        set_caption_emphasis(self.caption_labels, self._move_t, self.fg)

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

        font = preview_font(24)
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
                ink = outline_ink(font, ch)
                is_punct = (ci, i) in PUNCT_CELLS
                if is_punct:
                    self._draw_cell_guide(
                        p, QRectF(col_cx - CELL / 2, cell_top, CELL, CELL)
                    )
                    cx = col_cx - ink.width() / 2 - ink.left()
                    cy = cell_cy - ink.height() / 2 - ink.top()
                    ex = (col_cx + CELL / 2) - ink.width() - ink.left()
                    ey = cell_top - ink.top()
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
                # x/y 已是基线坐标（目标墨迹左上角 - 墨迹框偏移）
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
        paint_click_ring(p, self._combo_click_point(), self._ring_k, self.accent)

    def _paint_cursor(self, p: QPainter):
        paint_cursor(p, self._cursor, self._cursor_alpha, self._cursor_press,
                     self.fg)


class _DemoScene(QWidget):
    """设置行（真实 ConfigCheckBox）+ 预览卡 + 右侧文案的通用骨架。

    子类只填 ``CHECK_TEXT`` / ``CAPTIONS``（改前、改后两条）并覆写
    ``_paint_content``（在预览白卡里作画）与 ``_advance``（按帧号推进自己的
    状态），并把 ``N_FRAMES`` 设成自己的时长。光标与点击涟漪由本类统一驱
    动；``_t`` 是预览的过渡进度，也是两条文案明暗互换的进度（两者同步，
    见 ``set_caption_emphasis``）。
    """

    SIZE = (460, 262)
    N_FRAMES = 20
    ROW_Y = 18
    PANEL = (16, 58, 170, 182)   # 预览卡（模拟画布/漫画页）
    CHECK_X = 118                # 空标签行里复选框的起点（标签 110 + 间距 8）
    CHECK_TEXT = ""
    CAPTIONS = ()
    # 时间轴（与标点场景同一套观感语言）；MOVE 段 6 帧 ≈ 5 帧明暗互换
    F_ARRIVE = 6
    F_PRESS = 7
    F_CLICK = 8
    F_MOVE_START = 9
    F_MOVE_END = 14
    F_FADE = 15

    def __init__(self, parent=None):
        super().__init__(parent)
        w, h = self.SIZE
        self.setFixedSize(w, h)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.fg = get_theme_color(key="@qwidgetForegroundColor")
        self.accent = get_theme_color(key="@accentPrimary")
        self.border = get_theme_color(key="@borderColor")

        row_h = CONFIG_COMBOBOX_HEIGHT
        self.check = ConfigCheckBox(self.CHECK_TEXT)
        self.check.setParent(self)
        self.check.setFixedWidth(self.check.sizeHint().width() + 4)
        self.check.setGeometry(self.CHECK_X, self.ROW_Y, self.check.width(), row_h)
        self.check.setChecked(False)

        self._build_row_extra()

        self.caption_labels = make_caption_labels(self, self.fg, self.CAPTIONS)

        self._f = 0
        self._cursor = QPointF(w - 30, h - 26)
        self._cursor_alpha = 1.0
        self._cursor_press = False
        self._ring_k = 0.0
        self._ring_point = QPointF()
        self._t = 0.0        # 场景主进度：0 = 关闭态，1 = 开启态
        self._emph = 0.0     # 文案明暗互换进度：0 = 改前条亮，1 = 改后条亮

        # 光标/涟漪画在置顶透明覆盖层上，保证盖过真实子控件；
        # 覆盖层是裸 QWidget，必须显式抵消全局 QSS 的 QWidget 底色规则
        self._overlay = QWidget(self)
        self._overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._overlay.setStyleSheet("background: transparent;")
        self._overlay.setGeometry(0, 0, w, h)
        self._overlay.paintEvent = self._paint_overlay
        # 层级兜底：所有子控件建完后统一把覆盖层顶到最上层——
        # 下拉列表/光标要永远盖过包括文案 label 在内的一切子控件
        self._overlay.raise_()

    # ── 供子类覆写 ──────────────────────────────────────────────
    def _build_row_extra(self):
        """在设置行里复选框之后追加真实控件（如 tcy 行的「应用」按钮）。"""

    def _advance(self, f: int):
        """按帧号推进场景自有状态（子类覆写）。"""

    def _paint_content(self, p: QPainter):
        """在预览白卡内作画（子类覆写）。"""

    # ── 状态推进 ────────────────────────────────────────────────
    def set_state(self, f: int):
        self._f = f
        checked = f >= self.F_CLICK
        if self.check.isChecked() != checked:
            self.check.setChecked(checked)
        self._step_cursor(f, self._click_point(), self.F_ARRIVE, self.F_FADE,
                          (self.F_PRESS, self.F_CLICK),
                          (self.F_PRESS, self.F_CLICK))
        steps = max(1, self.F_MOVE_END - self.F_MOVE_START + 1)
        self._t = out_cubic((f - self.F_MOVE_START + 1) / steps)
        self._emph = self._t           # 明暗互换与预览过渡同步
        set_caption_emphasis(self.caption_labels, self._emph, self.fg)
        self._advance(f)

    def _click_point(self) -> QPointF:
        # 指示器 13x13（config/stylesheet.css 的 QCheckBox#ConfigCheckBox::indicator）
        return QPointF(self.check.x() + 10,
                       self.check.y() + self.check.height() / 2)

    def _step_cursor(self, f: int, target: QPointF, arrive: int, fade: int,
                     press_frames, ring_frames):
        """光标入场 → 停在 target；fade 之后淡出，press/ring 帧段出按下反馈。"""
        if f <= arrive:
            t = out_cubic(f / max(1, arrive))
            start = QPointF(self.width() - 30, self.height() - 26)
            self._cursor = start + (target - start) * t
        else:
            self._cursor = QPointF(target)
        self._cursor_alpha = (
            1.0 if f <= fade else 1.0 - clamp01((f - fade) / 4.0)
        )
        self._cursor_press = f in press_frames
        self._ring_point = QPointF(target)
        self._ring_k = 0.0
        for f0 in ring_frames:
            if f0 <= f <= f0 + 1:
                self._ring_k = (f - f0 + 1) / 2.0
                break

    # ── 绘制 ────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        px, py, pw, ph = self.PANEL
        p.setPen(QPen(self.border, 1))
        p.setBrush(QColor(255, 255, 255))
        p.drawRoundedRect(QRectF(px, py, pw, ph), 6, 6)
        self._paint_content(p)

    def _paint_overlay(self, event):
        p = QPainter(self._overlay)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_click_ring(p, self._ring_point, self._ring_k, self.accent)
        paint_cursor(p, self._cursor, self._cursor_alpha, self._cursor_press,
                     self.fg)

    def _demo_font(self, pixel_size: int) -> tuple:
        font = preview_font(pixel_size)
        return font, QFontMetricsF(font)

    def _ink_color(self, tint: float) -> QColor:
        """墨迹色：tint=0 常色，tint=1 主题强调色（切换过程做闪烁提示）。"""
        base = QColor(26, 26, 26)
        return QColor(
            int(base.red() + (self.accent.red() - base.red()) * tint),
            int(base.green() + (self.accent.green() - base.green()) * tint),
            int(base.blue() + (self.accent.blue() - base.blue()) * tint),
        )


class _VerticalColumnScene(_DemoScene):
    """竖排文字列场景的共用量：进给/列宽都按引擎的两把尺子量。

    - 整字进给 = ``CharFontFormat.tbr.height()``（「啊」「木」紧墨迹的并集，
      见 ui/text_engine/layout.py::CharFontFormat.tbr）；
    - 列宽 = 同一 tbr 的宽度（引擎的 ``line_width`` / ``base_width``）；
    - 墨迹摆位用字形轮廓（见 ``outline_ink``）。
    """

    FONT_PX = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self._font, self._fm = self._demo_font(self.FONT_PX)
        tbr = self._tbr()
        self._cell = tbr.height()
        self._col_w = tbr.width()
        self._col_cx = self.PANEL[0] + self.PANEL[2] / 2
        self._col_top = float(self.PANEL[1] + 20)
        self._guide_a = 0.0

    def _tbr(self) -> QRectF:
        """引擎 CharFontFormat.tbr：啊/木 紧墨迹的并集（进给与列宽的唯一来源）。"""
        fm = self._fm
        zhi = fm.tightBoundingRect("啊")
        mu = fm.tightBoundingRect("木")
        left = min(zhi.left(), mu.left())
        right = max(zhi.right(), mu.right())
        return QRectF(left, mu.top(), right - left, mu.height())

    def _guide_alpha(self, start: int, end: int) -> float:
        guide_in = clamp01((self._f - start + 2) / 3.0)
        guide_out = 1.0 - clamp01((self._f - end) / 3.0)
        return min(guide_in, guide_out)

    def _paint_content(self, p: QPainter):
        p.setFont(self._font)
        self._paint_column(p)


class PunctSpacingScene(_VerticalColumnScene):
    """「紧凑标点间距」开关前后：竖排、。的格子被收紧。

    几何照 ui/text_engine/vertical_layout.py::layoutBlock / updateDrawOffsets 的
    紧凑分支（真机探针实测，24px 字号下整字进给 23、紧凑进给 11.5）：
    - 紧凑进给 = min(全宽进给, max(tbr.height()/2, 墨迹在进给方向上的量))；
      「、。」直立不旋转，进给方向上的量就是墨迹**高度**（实测 5.66），
      于是取到半字高 11.5；
    - 引擎该分支不动墨迹位置：、。仍贴格顶右上角（Simplified 靠边分支），
      收缩的只是格子，于是后面的字整体上移——「多余间距」消失靠的是格变短。
    """

    N_FRAMES = 20
    CHECK_TEXT = "紧凑标点间距"
    COLUMN = "あ、い。"
    PUNCT = "、。"
    CAPTIONS = (
        "<b>改前：</b>、。各占一个整字格，格内留出大片空白",
        "<b>改后：</b>后续文字上移，标点周围不再有多余间距",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        ink = min(outline_ink(self._font, ch).height() for ch in self.PUNCT)
        self._compact = max(self._cell / 2.0, float(ink))

    def _advance(self, f: int):
        self._guide_a = self._guide_alpha(self.F_MOVE_START, self.F_MOVE_END)

    def _cells(self, t: float):
        """按进度返回每个字的 (格顶, 格高)。"""
        top = self._col_top
        cells = []
        for ch in self.COLUMN:
            height = self._cell
            if ch in self.PUNCT:
                height += (self._compact - self._cell) * t
            cells.append((ch, top, height))
            top += height
        return cells

    def _paint_column(self, p: QPainter):
        tint = math.sin(math.pi * clamp01(self._t))
        for ch, top, height in self._cells(self._t):
            cell = QRectF(self._col_cx - self._cell / 2, top, self._cell, height)
            if ch in self.PUNCT:
                draw_cell_guide(p, cell, self.accent, self._guide_a)
                draw_ink_top_right(p, self._fm, ch, cell, self._ink_color(tint),
                                   font=self._font)
            else:
                draw_ink_center(p, self._fm, ch, cell, self._ink_color(0.0),
                                font=self._font)


class BracketHalfwidthScene(_VerticalColumnScene):
    """「竖排压缩「」『』为半角样式」开关前后：括号格由全角收成墨迹宽。

    几何照 updateDrawOffsets 的旋转分支与 layoutBlock 的半角分支（真机探针实测，
    24px 字号：整字进给 23、括号自然进给 24、半角格高 = 括号墨迹宽 8）：
    - 关闭：格高 = 行自然进给（24）；「 的墨迹顺时针转 90° 后沿列方向落在
      格的中下段（引擎 xoff=0，墨迹左旁距原样保留），」 则贴格顶；
    - 开启：格高 = punc_rect(char)[0].width()（括号**紧墨迹**宽 8，进给尺子），
      后续字整体上移；「 再按 reduction = 自然进给 - 半角格高 沿列上移，
      重新贴住前一个字（updateDrawOffsets 的 fork 兼容补偿），」 不动。
    演示假设「紧凑标点间距」关闭——该开关开着时括号本来就被压到半字高
    （11.5），半角开关只剩 11.5→8 的细差，看不出本体。
    """

    N_FRAMES = 20
    CHECK_TEXT = "竖排文本中压缩「」『』为半角样式"
    COLUMN = "あ「い」"
    BRACKETS = "「」"
    OPENING = "「『"
    CAPTIONS = (
        "<b>改前：</b>括号按全角字格摆放，格里的空档就是「多余间距」",
        "<b>改后：</b>括号格变窄、开括号上移贴住前字，整列变短",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._natural = {
            ch: float(self._fm.horizontalAdvance(ch)) for ch in self.BRACKETS
        }
        self._half = {
            ch: float(self._fm.tightBoundingRect(ch).width())
            for ch in self.BRACKETS
        }

    def _advance(self, f: int):
        self._guide_a = self._guide_alpha(self.F_MOVE_START, self.F_MOVE_END)

    def _cells(self, t: float):
        top = self._col_top
        cells = []
        for ch in self.COLUMN:
            height = self._cell
            if ch in self.BRACKETS:
                height = self._natural[ch] + (self._half[ch] - self._natural[ch]) * t
            cells.append((ch, top, height))
            top += height
        return cells

    def _paint_column(self, p: QPainter):
        tint = math.sin(math.pi * clamp01(self._t))
        left = self._col_cx - self._col_w / 2
        for ch, top, height in self._cells(self._t):
            if ch in self.BRACKETS:
                cell = QRectF(left, top, self._col_w, height)
                draw_cell_guide(p, cell, self.accent, self._guide_a)
                opening = ch in self.OPENING
                # 半角补偿：格高收缩了多少，开括号就沿列上移多少
                shift = max(self._natural[ch] - height, 0.0) if opening else 0.0
                draw_rotated_ink(
                    p, outline_ink(self._font, ch), ch, left, self._col_w,
                    top, self._ink_color(tint), opening, shift,
                )
            else:
                cell = QRectF(left, top, self._col_w, height)
                draw_ink_center(p, self._fm, ch, cell, self._ink_color(0.0),
                                font=self._font)


class TateChuYokoScene(_VerticalColumnScene):
    """「自动直排内横排」：同一串数字由逐字竖堆叠并成一个直立横向单元。

    几何照 text_combine 路径（真机探针实测：两组字格 23 → 一个 23 高的合并格，
    合并格宽 = max(列宽, 自然横排宽度)，比列略宽）：
    - 关闭：每个字符独立成行，各占整字格，墨迹在格里居中；
    - 开启：整串成为一行多列（line.setNumColumns(字数)），格高不变、格宽按
      横排自然宽度取 max，横排墨迹在该格里居中（rendering/tate_chu_yoko.py
      的 tate_chu_yoko_transform 就是一次平移居中）。
    """

    N_FRAMES = 20
    CHECK_TEXT = "自动直排内横排"
    COLUMN = "12あ"
    RUN = "12"
    CAPTIONS = (
        "<b>改前：</b>数字逐个竖着堆叠，各占一个整字格",
        "<b>改后：</b>数字并排占一格，后面的字上移",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._run_w = float(self._fm.horizontalAdvance(self.RUN))
        self._run_ink = self._fm.tightBoundingRect(self.RUN)
        self._advances = [float(self._fm.horizontalAdvance(ch)) for ch in self.RUN]

    def _build_row_extra(self):
        # 真实设置行在这一项上还挂了「应用」按钮，且只在开关打开时出现
        # （ui/configpanel.py::on_auto_tate_chu_yoko_changed 的 setVisible）
        self._apply_btn = QPushButton("应用", self)
        self._apply_btn.setObjectName("ConfigButton")
        self._apply_btn.setFixedWidth(CONFIG_COMBOBOX_SHORT)
        self._apply_btn.setGeometry(
            self.check.x() + self.check.width() + 8, self.ROW_Y,
            CONFIG_COMBOBOX_SHORT, CONFIG_COMBOBOX_HEIGHT,
        )
        self._apply_btn.setVisible(False)

    def _advance(self, f: int):
        self._guide_a = self._guide_alpha(self.F_MOVE_START, self.F_MOVE_END)
        if self._apply_btn is not None:
            enabled = f >= self.F_CLICK
            if self._apply_btn.isVisible() != enabled:
                self._apply_btn.setVisible(enabled)

    def _cells(self, t: float):
        """合并格与后续字格：(文字, 格顶, 格高)。"""
        cells = [(self.RUN, self._col_top, self._cell)]
        tail_top = self._col_top + self._cell * (2.0 - t)
        cells.append((self.COLUMN[len(self.RUN)], tail_top, self._cell))
        return cells

    def _run_cell(self) -> QRectF:
        width = max(self._col_w, self._run_w)
        left = self._col_cx - self._col_w / 2 + (self._col_w - width) / 2
        return QRectF(left, self._col_top, width, self._cell)

    def _run_pen_points(self):
        """合并态里每个数字的基线落点（横排自然进给、整串在格里居中）。"""
        cell = self._run_cell()
        x = cell.center().x() - self._run_w / 2
        y = cell.center().y() - (self._run_ink.top() + self._run_ink.height() / 2)
        points = []
        for advance in self._advances:
            points.append(QPointF(x, y))
            x += advance
        return points

    def _stacked_pen_points(self):
        """逐字堆叠态里每个数字的基线落点（各自在整字格里居中）。"""
        points = []
        for index, ch in enumerate(self.RUN):
            cell = QRectF(
                self._col_cx - self._col_w / 2,
                self._col_top + index * self._cell,
                self._col_w, self._cell,
            )
            ink = outline_ink(self._font, ch)
            points.append(ink_pen(
                ink, cell.center().x() - ink.width() / 2,
                cell.center().y() - ink.height() / 2,
            ))
        return points

    def _paint_column(self, p: QPainter):
        tint = math.sin(math.pi * clamp01(self._t))
        stacked = self._stacked_pen_points()
        merged = self._run_pen_points()
        for index, ch in enumerate(self.RUN):
            cell = QRectF(
                self._col_cx - self._col_w / 2,
                self._col_top + index * self._cell,
                self._col_w, self._cell,
            )
            draw_cell_guide(p, cell, self.accent, self._guide_a * (1.0 - self._t))
            point = stacked[index] + (merged[index] - stacked[index]) * self._t
            p.setPen(self._ink_color(tint))
            p.drawText(point, ch)
        draw_cell_guide(p, self._run_cell(), self.accent,
                        self._guide_a * self._t)
        for text, top, height in self._cells(self._t):
            if text == self.RUN:
                continue
            cell = QRectF(self._col_cx - self._col_w / 2, top, self._col_w, height)
            draw_ink_center(p, self._fm, text, cell, self._ink_color(0.0),
                            font=self._font)


class _BadgePageScene(_DemoScene):
    """块徽标两个场景的共用版式：漫画页卡 + 两个文本框（内含竖排小字）。"""

    # 面板内偏移 (x, y, w, h, 右起两列示意文字)
    BLOCKS = (
        (94, 28, 58, 74, ("だめだよ", "！")),
        (20, 104, 58, 66, ("あぶない", "…")),
    )

    def _block_rect(self, block) -> QRectF:
        dx, dy, w, h, _text = block
        return QRectF(self.PANEL[0] + dx, self.PANEL[1] + dy, w, h)

    def _paint_page(self, p: QPainter):
        for block in self.BLOCKS:
            rect = self._block_rect(block)
            draw_block_frame(p, rect, TEXTRECT_SHOW_COLOR, 2.4)
            self._paint_block_text(p, rect, block[4])

    def _paint_block_text(self, p: QPainter, rect: QRectF, columns):
        font, fm = self._demo_font(13)
        p.setFont(font)
        cell, gap = 15.0, 4.0
        total = cell * len(columns) + gap * (len(columns) - 1)
        right = rect.center().x() + total / 2 - cell / 2   # 竖排右起
        top = rect.top() + 4
        ink = QColor(60, 60, 60)
        for index, text in enumerate(columns):
            cx = right - index * (cell + gap)
            for gi, ch in enumerate(text):
                place = QRectF(cx - cell / 2, top + gi * cell, cell, cell)
                if not rect.contains(place.center()):
                    continue
                draw_ink_center(p, fm, ch, place, ink, font=font)


class SeqBadgeScene(_BadgePageScene):
    """「序号徽标」：块左上角出现序号徽标。

    外观展示类（开关只是显示/不显示徽标），文案走单条常显说明、无明暗互换
    （``make_single_caption``）。徽标画法照 ui/text_engine/item.py::
    _OrderBadgeItem：字号 11 加粗、内边距 4/2、圆角 3、默认黑底 alpha170
    （选中时改主题色）、白字居中；锚点是块轮廓左上角——徽标自己的包围盒是
    (0, -h, w, h)，所以整体贴在块左上角**外上方**。
    """

    N_FRAMES = 20
    CHECK_TEXT = "序号徽标"
    CAPTIONS = ()   # 外观展示类：无改前/改后双条
    NOTE = "开启后：序号按阅读顺序贴在每块左上角"

    def __init__(self, parent=None):
        super().__init__(parent)
        make_single_caption(self, self.fg, self.NOTE)
        font = QFont(UI_FONT_FAMILY)
        font.setBold(True)
        font.setPixelSize(11)
        self._font = font
        self._metrics = QFontMetrics(font)

    def _badge_rect(self, number: int, block: QRectF, t: float) -> QRectF:
        text = str(number)
        width = self._metrics.horizontalAdvance(text) + 2 * BADGE_H_PAD
        height = self._metrics.height() + 2 * BADGE_V_PAD
        # 入场：从上方 5px 滑落到位上（底色淡入见 _paint_content）
        return QRectF(block.left(), block.top() - height - 5.0 * (1.0 - t),
                      width, height)

    def _paint_content(self, p: QPainter):
        self._paint_page(p)
        alpha = out_cubic(clamp01(self._t))
        if alpha <= 0.01:
            return
        for index, block in enumerate(self.BLOCKS):
            rect = self._badge_rect(index + 1, self._block_rect(block), self._t)
            bg = QColor(SEQ_BADGE_COLOR)
            bg.setAlpha(int(SEQ_BADGE_COLOR.alpha() * alpha))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(rect, 3, 3)
            text = QColor(255, 255, 255, int(255 * alpha))
            p.setPen(text)
            p.setFont(self._font)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(index + 1))


class TagBadgeScene(_BadgePageScene):
    """「标签徽标」：块右上角出现标签徽标。

    外观展示类（开关只是显示/不显示徽标），文案走单条常显说明、无明暗互换
    （``make_single_caption``）。徽标画法照 ui/textitem.py::_TagBadgeItem：
    与序号徽标同一套尺寸，锚点是块轮廓右上角（包围盒 (0, -h, w, h) 使右下角
    对齐块右上角）；底色按标签性质取疑点暖色 / 指示冷色，多标签时字形连排。
    """

    N_FRAMES = 20
    CHECK_TEXT = "标签徽标"
    TAGS = (("!", TAG_BADGE_DOUBT_COLOR), ("♪", TAG_BADGE_DIRECTIVE_COLOR))
    CAPTIONS = ()   # 外观展示类：无改前/改后双条
    NOTE = "开启后：标签字形贴在块右上角，疑点暖色、指示冷色"

    def __init__(self, parent=None):
        super().__init__(parent)
        make_single_caption(self, self.fg, self.NOTE)
        font = QFont(UI_FONT_FAMILY)
        font.setPixelSize(11)
        self._font = font
        self._metrics = QFontMetrics(font)

    def _badge_rect(self, glyphs: str, block: QRectF, t: float) -> QRectF:
        width = self._metrics.horizontalAdvance(glyphs) + 2 * BADGE_H_PAD
        height = self._metrics.height() + 2 * BADGE_V_PAD
        return QRectF(block.right() - width,
                      block.top() - height - 5.0 * (1.0 - t), width, height)

    def _paint_content(self, p: QPainter):
        self._paint_page(p)
        alpha = out_cubic(clamp01(self._t))
        if alpha <= 0.01:
            return
        for index, block in enumerate(self.BLOCKS):
            glyphs, color = self.TAGS[index]
            rect = self._badge_rect(glyphs, self._block_rect(block), self._t)
            bg = QColor(color)
            bg.setAlpha(int(color.alpha() * alpha))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(rect, 3, 3)
            p.setPen(QColor(255, 255, 255, int(255 * alpha)))
            p.setFont(self._font)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, glyphs)


class ClipTextScene(_DemoScene):
    """「溢出裁剪」三拍演示：先展示关闭态行为，再对比开启态行为。

    - 第一拍（开关**关闭**）：译文超出块边界 → 块自动放大到放得下文字，
      无黄框（关闭态行为）；
    - 第二拍：光标点开开关 → 同样的文字被裁切 + 黄框警示（开启态行为）；
    - 第三拍：光标拖角部手柄放大块 → 放得下、黄框消失（过程拍，无独立文案）。
    字幕保持行为对比类的改前/改后双条 + 明暗互换（第二拍点开关时互换），
    两拍各配一条，第三拍行为自明。

    画法照 ui/textitem.py：溢出时文字裁到块内容框、描边换成黄框
    QColor(255,200,0,200)、线宽 3.5；放得下后描边回到常规选中态
    （TEXTRECT_SELECTED_COLOR 虚线）；角部手柄照
    ui/text_engine/shape_control.py::ControlBlockItem 实画 15px
    （CBEDGE_WIDTH/2）的浅灰方块、2px 深灰边。
    """

    N_FRAMES = 36
    CHECK_TEXT = "溢出裁剪"
    TEXT = "だめだよ！"
    BOX = (62, 22, 52, 92)         # 预览卡内的内容框（x, y, w, h）
    BOX_H_END = 134.0              # 放得下 5 个字的高度
    CELL = 26.0
    FONT_PX = 22
    CAPTIONS = (
        "<b>改前：</b>译文超出块边界时，块自动放大到放得下文字",
        "<b>改后：</b>同样的文字被裁切到框内，描边变<b>黄框</b>警示",
    )
    # 时间轴（@10fps）：三拍
    F_GROW_END = 5            # 第一拍：关闭态块自动长高到放得下文字
    F_CB_ARRIVE = 7           # 光标抵达复选框
    F_CB_PRESS = 8            # 按下复选框
    F_CLICK = 9               # 点开「溢出裁剪」→ 第二拍开始
    F_SHRINK_END = 13         # 块缩回设定尺寸，文字被裁切 + 黄框（互换同期完成）
    F_HANDLE_MOVE_START = 14  # 光标开始移向角部手柄
    F_HANDLE_PRESS = 19       # 按下手柄 → 第三拍开始
    F_DRAG_START = 20
    F_DRAG_END = 31           # 拖拽放大完成，黄框消失
    F_FADE = 31               # 光标淡出（32～35 共 4 帧）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._font, self._fm = self._demo_font(self.FONT_PX)
        self._h = float(self.BOX[3])   # 当前块高（set_state 按帧推进）

    # ── 几何 ────────────────────────────────────────────────────
    def _box_rect(self) -> QRectF:
        x, y, w, _h = self.BOX
        return QRectF(self.PANEL[0] + x, self.PANEL[1] + y, w, self._h)

    def _text_height(self) -> float:
        return 1.0 + len(self.TEXT) * self.CELL   # 首字上边距 1px

    def _fits(self) -> bool:
        return self._h >= self._text_height()

    def _handle_center(self) -> QPointF:
        corner = self._box_rect().bottomRight()
        return QPointF(corner.x() + HANDLE_SIZE / 2,
                       corner.y() + HANDLE_SIZE / 2)

    def _height_at(self, f: int) -> float:
        """块高按拍推进：长高（关闭态）→ 缩回（点开裁剪）→ 再长高（手柄拖拽）。"""
        small, fit = float(self.BOX[3]), self.BOX_H_END
        if f <= self.F_GROW_END:
            grow = out_cubic((f + 1) / (self.F_GROW_END + 1))
            return small + (fit - small) * grow
        if f < self.F_CLICK:
            return fit
        if f < self.F_DRAG_START:
            shrink = out_cubic(
                (f - self.F_CLICK + 1) / (self.F_SHRINK_END - self.F_CLICK + 1)
            )
            return fit + (small - fit) * shrink
        drag = out_cubic(
            (f - self.F_DRAG_START + 1)
            / (self.F_DRAG_END - self.F_DRAG_START + 1)
        )
        return small + (fit - small) * drag

    # ── 状态推进 ────────────────────────────────────────────────
    def set_state(self, f: int):
        self._f = f
        checked = f >= self.F_CLICK
        if self.check.isChecked() != checked:
            self.check.setChecked(checked)
        self._h = self._height_at(f)
        # 明暗互换跟「开关点开」走：改前条讲关闭态行为、改后条讲开启态行为，
        # 第三拍是过程、不另配文案
        emph = 0.0
        if f >= self.F_CLICK:
            emph = out_cubic(
                (f - self.F_CLICK + 1) / (self.F_SHRINK_END - self.F_CLICK + 1)
            )
        self._emph = emph
        set_caption_emphasis(self.caption_labels, self._emph, self.fg)
        # 光标：入场 → 复选框 → 角部手柄（拖拽期骑在手柄上随块移动）
        start = QPointF(self.width() - 30, self.height() - 26)
        cb = self._click_point()
        handle = self._handle_center()
        if f <= self.F_CB_ARRIVE:
            t = out_cubic(f / self.F_CB_ARRIVE)
            self._cursor = start + (cb - start) * t
        elif f < self.F_HANDLE_MOVE_START:
            self._cursor = cb
        elif f < self.F_HANDLE_PRESS:
            t = out_cubic(
                (f - self.F_HANDLE_MOVE_START + 1)
                / (self.F_HANDLE_PRESS - self.F_HANDLE_MOVE_START)
            )
            self._cursor = cb + (handle - cb) * t
        else:
            self._cursor = handle
        self._cursor_press = f in (self.F_CB_PRESS, self.F_HANDLE_PRESS)
        self._cursor_alpha = (
            1.0 if f <= self.F_FADE else 1.0 - clamp01((f - self.F_FADE) / 4.0)
        )
        self._ring_k = 0.0
        for f0, point in ((self.F_CB_PRESS, cb), (self.F_HANDLE_PRESS, handle)):
            if f0 <= f <= f0 + 1:
                self._ring_k = (f - f0 + 1) / 2.0
                self._ring_point = point
                break
        else:
            self._ring_point = handle

    # ── 绘制 ────────────────────────────────────────────────────
    def _paint_content(self, p: QPainter):
        rect = self._box_rect()
        # 裁切 + 黄框只在开启态且放不下时出现；关闭态块自己长到放得下、永不裁
        clipped = self.check.isChecked() and not self._fits()
        if clipped:
            p.save()
            p.setClipRect(rect)
            self._paint_text(p, rect)
            p.restore()
            draw_block_frame(p, rect, CLIP_WARN_COLOR, 3.5)
        else:
            self._paint_text(p, rect)
            draw_block_frame(p, rect, TEXTRECT_SELECTED_COLOR, 3.5, dashed=True)
        corner = rect.bottomRight()
        p.setPen(QPen(HANDLE_BORDER_COLOR, 2))
        p.setBrush(HANDLE_FILL_COLOR)
        p.drawRect(QRectF(corner.x(), corner.y(), HANDLE_SIZE, HANDLE_SIZE))

    def _paint_text(self, p: QPainter, rect: QRectF):
        p.setFont(self._font)
        top = rect.top() + 1
        for ch in self.TEXT:
            cell = QRectF(rect.center().x() - self.CELL / 2, top,
                          self.CELL, self.CELL)
            draw_ink_center(p, self._fm, ch, cell, QColor(26, 26, 26),
                            font=self._font)
            top += self.CELL


SCENES = {
    "punctuation_position": PunctuationScene,
    "punct_spacing": PunctSpacingScene,
    "bracket_halfwidth": BracketHalfwidthScene,
    "tcy": TateChuYokoScene,
    "block_index": SeqBadgeScene,
    "tag_badge": TagBadgeScene,
    "clip_text": ClipTextScene,
}
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
    # 必须拷贝成自持内存：arr 只是 QImage 内存的视图，grab() 的临时 QImage
    # 一析构这块内存就归 Qt 复用，等最后统一编码 WebP 时读到的是被覆写的
    # 像素——实测会 access violation 崩在 Pillow 的 tobytes，即便侥幸不崩
    # 产物也可能是脏帧。
    return Image.frombytes("RGBA", (w, h), arr.tobytes())


def _save_webp(frames: list, out: str, fps: int) -> float:
    """编码并落盘，返回产物体积 KB。

    先写 ``<out>.tmp`` 再 ``os.replace``：显示侧 QMovie 可能仍握着旧文件句柄，
    直接覆写会失败。replace 失败（Windows 上是 PermissionError/OSError）重试
    一次，仍失败则抛出、由调用方按场景点名，不中断其他场景。
    """
    os.makedirs(osp.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    frames[0].save(
        tmp,
        "WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=int(1000 / fps),
        loop=0,
        lossless=True,
        method=6,
        exact=True,
    )
    try:
        os.replace(tmp, out)
    except OSError:
        time.sleep(0.5)
        try:
            os.replace(tmp, out)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
    return osp.getsize(out) / 1024


def _render_frames(app: QApplication, scene, dump_dir: str = None) -> list:
    frames = []
    for f in range(scene.N_FRAMES):
        scene.set_state(f)
        app.processEvents()
        frames.append(qimage_to_pil(scene.grab().toImage()))
        if dump_dir:
            os.makedirs(dump_dir, exist_ok=True)
            frames[-1].save(osp.join(dump_dir, f"frame_{f:03d}.png"))
    return frames


def _parse_args():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scene", default="punctuation_position", choices=sorted(SCENES))
    ap.add_argument("--all", action="store_true", help="依次生成 SCENES 全部场景")
    ap.add_argument("--out", default=None, help="输出 .webp 路径（--all 时忽略）")
    ap.add_argument("--out-dir", default=None,
                    help="--all 的输出目录（每场景写 <dir>/<key>.webp），"
                         "默认 config/help_anims/（仓库跟踪的固化默认）；"
                         "设置页按钮传 config/help_anims_local/（本机覆盖层）")
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--theme", default="", help="主题名，默认取当前配置")
    ap.add_argument("--dump-frames", dest="dump_frames", default=None,
                    help="逐帧导出 PNG 目录（--all 时按 <目录>/<key> 分场景）")
    ap.add_argument("--platform", default=None, choices=["windows", "offscreen"],
                    help="Qt 平台插件，默认 windows（offscreen 仅供调试）")
    return ap.parse_args()


def main():
    args = _parse_args()
    if args.platform:
        os.environ["QT_QPA_PLATFORM"] = args.platform
        if args.platform == "offscreen":
            os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

    load_config()
    app = QApplication.instance() or QApplication(sys.argv)
    # UI 标签（QLabel 等）字体显式钉到 GUI 真实默认家族：offscreen 的"系统
    # 默认"落到 Arial 是发虚根因之一。只钉家族、不动解析出的字号，windows
    # 平台下本就解析成该家族，此处是跨平台兜底。
    ui_font = app.font()
    ui_font.setFamily(UI_FONT_FAMILY)
    app.setFont(ui_font)
    app.setStyleSheet(parse_stylesheet(args.theme))

    keys = sorted(SCENES) if args.all else [args.scene]
    dpr = app.primaryScreen().devicePixelRatio() if app.screens() else 1.0
    print(f"生成 DPR {dpr:g}（产物物理尺寸 = 460x262 逻辑 × DPR）")
    t0 = time.perf_counter()
    failures = []
    for key in keys:
        try:
            scene = SCENES[key]()
            # 不 show（windows 平台下不 show 也能 grab，无闪窗）；polish 兜底
            # 保证 QSS/字体解析与 show 后一致，几何与旧产物零漂移
            scene.ensurePolished()
            app.processEvents()
            dump_dir = (
                osp.join(args.dump_frames, key)
                if args.dump_frames and args.all else args.dump_frames
            )
            frames = _render_frames(app, scene, dump_dir)
            out = args.out if (not args.all and args.out) else osp.join(
                args.out_dir or DEFAULT_OUT_DIR, f"{key}.webp"
            )
            size_kb = _save_webp(frames, out, args.fps)
            print(f"OK {key}  {frames[0].width}x{frames[0].height} px  "
                  f"{size_kb:.1f} KB  {scene.N_FRAMES} frames @ {args.fps}fps")
        except Exception as exc:  # noqa: BLE001 单场景失败不拖垮整批
            failures.append((key, exc))
            print(f"FAIL {key}  {exc}")
    elapsed = time.perf_counter() - t0
    if args.all:
        print(f"DONE {len(keys) - len(failures)}/{len(keys)}  {elapsed:.1f}s")
    else:
        print(f"耗时 {elapsed:.1f}s")
    if failures:
        print("失败场景：" + "; ".join(f"{k}（{e}）" for k, e in failures))
        sys.exit(1)


if __name__ == "__main__":
    main()
