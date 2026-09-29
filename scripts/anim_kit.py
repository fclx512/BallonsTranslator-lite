"""演示动画共享机制库（无任何版式假设）。

弹层流程（``scripts/gen_help_anim.py``）与未来的 README 大画幅流程共用本模块：
确定性时间轴原语、光标编排（``CursorPlan``）、场景机制基类（``AnimScene``）、
文案组件、与渲染引擎对齐的绘制小件、渲染/编码管线。画布尺寸、文案位置、
叙事节奏全部由调用方/场景声明——本模块不固化任何一种演示形态。

- 帧号确定性步进：所有动画状态由帧号推算（``AnimScene.set_state``），不依赖
  真实时钟，产物可复现；界面改版后重跑生成脚本即再生成。
- 文案强调用 QLabel.setStyleSheet 调 rgba 前景色 alpha，**禁止
  QGraphicsOpacityEffect**（图形效果接管重绘后在透明窗口上不可靠，见
  ui/configpanel.py::ConfigNotePopup）。
- 竖排几何两把尺子（与 ui/text_engine 引擎一致，别混用）：**进给/格高**用
  ``QFontMetricsF.tightBoundingRect``（ui/text_engine/layout.py::get_punc_rect
  用的就是它），**墨迹摆位**用字形真实轮廓（``outline_ink``，引擎画的是向量
  轮廓）——同一字形两者能差 ~1px。
"""

import os
import os.path as osp
import sys
import time
from typing import NamedTuple, Optional

ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
from PIL import Image
from qtpy.QtCore import QPointF, QRectF, Qt
from qtpy.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
)
from qtpy.QtWidgets import QApplication, QLabel, QWidget

from ui.misc import get_theme_color, parse_stylesheet
from utils.config import load_config


def clamp01(v):
    return max(0.0, min(1.0, v))


def out_cubic(t):
    t = clamp01(t)
    return 1.0 - (1.0 - t) ** 3


def move_progress(f: int, start: int, end: int) -> float:
    """[start, end] 帧间的推进进度（0→1，out_cubic）。

    公式 = out_cubic((f - start + 1) / (end - start + 1))：start 帧就有一格
    进度、到 end 帧恰好为 1——全部场景的过渡进度 ``_t`` 与文案明暗互换同用
    这一口径。
    """
    steps = max(1, end - start + 1)
    return out_cubic(clamp01((f - start + 1) / steps))


def guide_alpha(f: int, start: int, end: int) -> float:
    """字格虚线提示的淡入淡出：start 前后 3 帧淡入、end 后 3 帧淡出，重叠取小。"""
    guide_in = clamp01((f - start + 2) / 3.0)
    guide_out = 1.0 - clamp01((f - end) / 3.0)
    return min(guide_in, guide_out)


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
# 引擎：进给/格高来自 QFontMetricsF.tightBoundingRect（ui/text_engine/layout.py::
# get_punc_rect，结果按整数取整）；墨迹摆位来自向量轮廓
# （ui/text_engine/rendering/glyph.py::glyph_geometry）。
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


# ── 光标绘制小件 ────────────────────────────────────────────────

def _cursor_path(point: QPointF, scale: float) -> QPainterPath:
    """鼠标箭头路径。"""
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


# ── 光标编排 ────────────────────────────────────────────────────

class CursorPose(NamedTuple):
    """``CursorPlan.pose`` 的快照：位置 / 不透明度 / 按下态 / 涟漪进度与落点。"""

    point: QPointF
    alpha: float
    pressed: bool
    ring_k: float
    ring_point: QPointF


class CursorPlan:
    """光标行程的确定性编排：一串航点 + 按下/涟漪/淡出的帧号。

    帧语义（与既有场景逐位对齐）：
    - 首段移动 ``out_cubic(f / arrive)``（f=0 在起点、arrive 帧恰好到位）；
    - 后续段 ``out_cubic((f - seg0) / (arrive - seg0))``，seg0 = 上一航点的
      到达帧，传 ``start`` 可显式指定段起点（复现旧场景的非对称段）；
    - ``press(f0)``：f0 帧按下、涟漪画 [f0, f0+1] 两帧（k=(f-f0+1)/2）；
    - ``fade(f)``：f 帧之后 ``frames`` 帧内线性淡出。
    ``go``/``press`` 的落点支持 QPointF 或返回 QPointF 的 callable（目标本身
    随动画移动时用，如拖拽跟随角部手柄）。
    """

    def __init__(self, start: QPointF):
        self._start = QPointF(start)
        self._stops = []      # (arrive, seg0, target)
        self._presses = []    # (f0, ring_point 或 None)
        self._fade = None     # (f, frames)
        self._last_arrive = 0

    def go(self, target, arrive: int, start: Optional[int] = None):
        seg0 = self._last_arrive if start is None else start
        self._stops.append((arrive, seg0, target))
        self._last_arrive = arrive
        return self

    def press(self, f0: int, at=None):
        """点击事件；``at`` 显式给涟漪落点（默认光标在 f0 帧的位置）。"""
        self._presses.append((f0, at))
        return self

    def fade(self, f: int, frames: int = 4):
        self._fade = (f, frames)
        return self

    def pose(self, f: int) -> CursorPose:
        alpha = 1.0
        if self._fade is not None:
            fade_f, frames = self._fade
            if f > fade_f:
                alpha = 1.0 - clamp01((f - fade_f) / frames)
        pressed = any(f == f0 for f0, _at in self._presses)
        ring_k = 0.0
        ring_point = self._position_at(f)
        for f0, at in self._presses:
            if f0 <= f <= f0 + 1:
                ring_k = (f - f0 + 1) / 2.0
                if at is not None:
                    ring_point = QPointF(at()) if callable(at) else QPointF(at)
                else:
                    ring_point = self._position_at(f0)
                break
        return CursorPose(self._position_at(f), alpha, pressed, ring_k,
                          ring_point)

    def _position_at(self, f: int) -> QPointF:
        point = QPointF(self._start)
        for arrive, seg0, target in self._stops:
            tgt = target() if callable(target) else target
            tgt = QPointF(tgt)
            if f >= arrive:
                point = tgt   # 已到位（目标可动时随动），看下一航点
            else:
                span = max(1, arrive - seg0)
                t = out_cubic(clamp01((f - seg0) / span))
                return point + (tgt - point) * t
        return point


# ── 场景机制基类 ────────────────────────────────────────────────

class AnimScene(QWidget):
    """演示场景机制基类：画布尺寸/时长场景自声明，覆盖层与光标统一驱动。

    子类在 ``_build()`` 里搭自己的版式与控件（本基类不做任何版式假设——
    弹层的"设置行 + 预览卡 + 文案"只是 gen_help_anim.py 里的一个预设编排）；
    需要光标就把 ``CursorPlan`` 赋给 ``self.plan``。置顶覆盖层上先画场景
    置顶内容（``_paint_overlay_content``，如要盖过真实子控件的下拉列表），
    再画涟漪与光标。
    """

    SIZE = (460, 262)
    N_FRAMES = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        w, h = self.SIZE
        self.setFixedSize(w, h)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.fg = get_theme_color(key="@qwidgetForegroundColor")
        self.accent = get_theme_color(key="@accentPrimary")
        self.border = get_theme_color(key="@borderColor")
        self.card = get_theme_color(key="@qwidgetBackgroundColor")

        self.plan = None
        self._f = 0
        self._pose = None
        self._build()

        # 光标/涟漪等画在置顶的透明覆盖层上，保证盖过真实子控件；
        # 覆盖层是裸 QWidget，必须显式抵消全局 QSS 的 QWidget 底色规则
        self._overlay = QWidget(self)
        self._overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._overlay.setStyleSheet("background: transparent;")
        self._overlay.setGeometry(0, 0, w, h)
        self._overlay.paintEvent = self._paint_overlay_event
        # 层级兜底：所有子控件建完后统一把覆盖层顶到最上层——
        # 下拉列表/光标要永远盖过包括文案 label 在内的一切子控件
        self._overlay.raise_()

    # ── 供子类覆写 ──────────────────────────────────────────────
    def _build(self):
        """场景版式与子控件（子类覆写；调用时主题色已就绪）。"""

    def _update(self, f: int):
        """按帧号推进场景自有状态（子类覆写）。"""

    def _paint_overlay_content(self, p: QPainter):
        """覆盖层上的场景置顶内容（先画，涟漪/光标在其后）。"""

    # ── 状态推进与绘制 ──────────────────────────────────────────
    def set_state(self, f: int):
        self._f = f
        self._update(f)
        if self.plan is not None:
            self._pose = self.plan.pose(f)

    def _paint_overlay_event(self, event):
        p = QPainter(self._overlay)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_overlay_content(p)
        if self._pose is None:
            return
        if self._pose.ring_k > 0:
            paint_click_ring(p, self._pose.ring_point, self._pose.ring_k,
                             self.accent)
        if self._pose.alpha > 0.01:
            paint_cursor(p, self._pose.point, self._pose.alpha,
                         self._pose.pressed, self.fg)


# ── 文案组件 ────────────────────────────────────────────────────

def set_caption_emphasis(labels, k: float, color: QColor):
    """两条常显文案的明暗互换：未激活一条降到约 35% 不透明度，激活的全亮。

    k=0 改前条全亮（改后条暗），k=1 反转；点击复选框/下拉项后用 out_cubic
    约 5 帧交叉淡化互换，与预览过渡同步。
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


def make_caption_labels(parent: QWidget, fg: QColor, captions,
                        x: float, ys, w: float, h: float) -> list:
    """两条常显文案（改前在上、改后在下）；几何由调用方给。返回 labels。

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
        label.setGeometry(x, ys[index], w, h)
        label.setText(text)
        labels.append(label)
    set_caption_emphasis(labels, 0.0, fg)
    return labels


def make_single_caption(parent: QWidget, fg: QColor, text: str,
                        x: float, y: float, w: float, h: float) -> QLabel:
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
    label.setGeometry(x, y, w, h)
    rgb = f"{fg.red()},{fg.green()},{fg.blue()}"
    label.setStyleSheet(
        f"color: rgba({rgb},210);"
        "background: transparent; font-size: 12px;"
    )
    label.setText(text)
    return label


# ── 与引擎对齐的绘制小件 ────────────────────────────────────────

def draw_cell_guide(painter: QPainter, rect: QRectF, color: QColor, alpha: float):
    """虚线字格提示（强调「这个字占多大格」）。"""
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

    引擎居中用的是**轮廓**墨迹（ui/text_engine/vertical_layout.py::
    updateDrawOffsets 的 act_rect），给了 font 就按轮廓摆，否则退回
    tightBoundingRect（徽标等小字示意用）。
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


# ── 字体 ────────────────────────────────────────────────────────

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


# ── 渲染 / 编码管线 ─────────────────────────────────────────────

def init_app(theme: str = "") -> QApplication:
    """生成器共用的 QApplication 样板：读配置、字体钉 GUI 默认家族、挂样式表。"""
    load_config()
    app = QApplication.instance() or QApplication(sys.argv)
    # UI 标签（QLabel 等）字体显式钉到 GUI 真实默认家族：offscreen 的"系统
    # 默认"落到 Arial 是发虚根因之一。只钉家族、不动解析出的字号，windows
    # 平台下本就解析成该家族，此处是跨平台兜底。
    ui_font = app.font()
    ui_font.setFamily(UI_FONT_FAMILY)
    app.setFont(ui_font)
    app.setStyleSheet(parse_stylesheet(theme))
    return app


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


def iter_frames(app: QApplication, scene: AnimScene, dump_dir: str = None,
                camera=None):
    """按帧号确定性步进逐帧 grab（不 show，windows 平台下无闪窗）。

    生成器形态：大画幅场景全帧留内存会到几百 MB，改为一帧一产出、由编码端
    消费。``camera(f) -> QRectF|None`` 为可选视口钩子（README 大画幅的
    「镜头跟随光标」）：返回逻辑坐标裁切矩形时按其裁切输出，None 输出整幅。
    裁切在 grab 后立即做，迭代过程中至多持有当前一帧。
    """
    for f in range(scene.N_FRAMES):
        scene.set_state(f)
        app.processEvents()
        frame = qimage_to_pil(scene.grab().toImage())
        if camera is not None:
            rect = camera(f)
            if rect is not None:
                scale = frame.width / scene.width()
                x0 = round(rect.x() * scale)
                y0 = round(rect.y() * scale)
                w = round(rect.width() * scale)
                h = round(rect.height() * scale)
                frame = frame.crop((
                    max(0, x0), max(0, y0),
                    min(frame.width, x0 + w), min(frame.height, y0 + h),
                ))
        if dump_dir:
            os.makedirs(dump_dir, exist_ok=True)
            frame.save(osp.join(dump_dir, f"frame_{f:03d}.png"))
        yield frame


def render_frames(app: QApplication, scene: AnimScene, dump_dir: str = None,
                  camera=None) -> list:
    """``iter_frames`` 的列表形态（弹层流程既有调用面保持不变）。"""
    return list(iter_frames(app, scene, dump_dir, camera))


def save_webp(frames, out: str, fps: int, lossless: bool = True) -> float:
    """编码并落盘，返回产物体积 KB。

    ``frames`` 接受任意可迭代（list 或生成器）：生成器形态下 Pillow 逐帧
    消费、内存至多持有当前一帧，大画幅长时长的唯一可行形态。
    先写 ``<out>.tmp`` 再 ``os.replace``：显示侧 QMovie 可能仍握着旧文件句柄，
    直接覆写会失败。replace 失败（Windows 上是 PermissionError/OSError）重试
    一次，仍失败则抛出、由调用方按场景点名，不中断其他场景。弹层产物走无损
    （纯色 UI 内容下体积最小）；README 大画幅流程允许 lossless=False。
    """
    os.makedirs(osp.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    it = iter(frames)
    first = next(it)
    first.save(
        tmp,
        "WEBP",
        save_all=True,
        append_images=it,
        duration=int(1000 / fps),
        loop=0,
        lossless=lossless,
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
