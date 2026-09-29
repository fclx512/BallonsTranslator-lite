"""备注问号弹层演示动画生成器（离屏渲染真实控件 → 无损动画 WebP）。

用法：
    ./ballontrans_pylibs_win/python.exe scripts/gen_help_anim.py --scene punctuation_position
    可选：--all（依次生成 SCENES 全部场景）/ --out 路径 / --out-dir 目录 /
         --fps 帧率 / --theme 主题名（默认取当前配置主题）/
         --platform windows|offscreen / --dump-frames 目录（逐帧导出 PNG，供目视检查）

机制（确定性时间轴原语、光标编排 CursorPlan、场景基类 AnimScene、文案组件、
渲染/编码管线）在 ``scripts/anim_kit.py``，本文件只保留弹层流程：460×262
版式常量、场景与 CLI。

弹层管线要点：
- QT_QPA_PLATFORM 默认 windows（原生 DPR 就是真实屏幕值，**不设** QT_SCALE_FACTOR
  避免叠乘）；显式覆盖用环境变量或 ``--platform offscreen``（调试用）。windows
  平台下不 show 窗口也能直接 grab()，无闪窗。
- 产物分辨率 = 460×262 × 渲染 DPR，**DPR 双轨**（``_resolve_dpr``）：写仓库默认
  目录自动**钉定 DPR 1.25**（QT_ENABLE_HIGHDPI_SCALING=0 + QT_SCALE_FACTOR=1.25，
  先于 QApplication 设置）——入库产物跨工作机逐字节一致；其余输出（本机覆盖层
  config/help_anims_local/、显式 --out/--out-dir）按本机屏幕真实 DPR（覆盖层的
  意义就是本机适配）；``--dpr`` 显式覆盖。显示侧取屏幕 DPR 折算逻辑尺寸
  （ui/configpanel.py::ConfigNotePopup），生成 DPR = 显示 DPR 时 1 图像像素 =
  1 设备像素；钉定产物换机器显示时用设置页的重新生成按钮重出本机适配版。
- 样式复用 config/stylesheet.css + 用户主题（ui.misc.parse_stylesheet），
  组合框等直接用 ui/custom_widget 封装控件，保证与设置页观感一致。
- 场景形态：``punctuation_position``（下拉选择叙事）直接继承 AnimScene 自行
  组装；其余 6 个走 ``CheckboxDemoScene`` 预设（复选框单点叙事：光标入场 →
  点击 → 过渡 → 文案明暗互换）。**不匹配该叙事的功能别硬套**——参照
  PunctuationScene 的做法用 AnimScene + 组件自行组装。
- 文案方案分两类：**行为对比类**（开关改变渲染行为，如标点布局/引号宽度/
  tcy/裁剪/块放大）用改前/改后两条**从头到尾同时显示**（改前在上、改后在
  下），未激活一条降到约 35% 不透明度、激活的全亮；点击后两条用 out_cubic
  约 5 帧交叉淡化互换强调状态，与预览过渡同步。**外观展示类**（开/关只是
  显示/不显示某个装饰，如序号/标签徽标）改单条常显说明，无前缀无互换。
  都用 rgba 前景色调 alpha，**不用** QGraphicsOpacityEffect、**没有**高亮
  边框。
- 竖排列的几何一律照 ui/text_engine/vertical_layout.py::layoutBlock /
  updateDrawOffsets 的数学（两把尺子的口径见 anim_kit 模块注释）。改动这些
  场景后跑一次性探针与真机引擎逐字比对（见 docs 使用说明「验收」）。
- 每个场景自带 N_FRAMES（时长不同），main() 按场景类属性循环。

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

ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
# 便携解释器带 PYTHONSAFEPATH，脚本目录不自动进 sys.path（anim_kit 同目录）
SCRIPT_DIR = osp.dirname(osp.abspath(__file__))
for _p in (SCRIPT_DIR, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from qtpy.QtCore import QPointF, QRectF, Qt
from qtpy.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QFontMetricsF,
    QPainter,
    QPen,
)
from qtpy.QtWidgets import QLabel, QPushButton

from anim_kit import (
    BADGE_H_PAD,
    BADGE_V_PAD,
    CLIP_WARN_COLOR,
    CursorPlan,
    AnimScene,
    HANDLE_BORDER_COLOR,
    HANDLE_FILL_COLOR,
    HANDLE_SIZE,
    SEQ_BADGE_COLOR,
    TAG_BADGE_DIRECTIVE_COLOR,
    TAG_BADGE_DOUBT_COLOR,
    TEXTRECT_SELECTED_COLOR,
    TEXTRECT_SHOW_COLOR,
    UI_FONT_FAMILY,
    clamp01,
    draw_block_frame,
    draw_cell_guide,
    draw_ink_center,
    draw_rotated_ink,
    draw_ink_top_right,
    guide_alpha,
    init_app,
    ink_pen,
    make_caption_labels,
    make_single_caption,
    move_progress,
    out_cubic,
    outline_ink,
    preview_font,
    render_frames,
    save_webp,
    set_caption_emphasis,
)
from ui.custom_widget import ConfigCheckBox, ConfigComboBox
from utils.shared import CONFIG_COMBOBOX_HEIGHT, CONFIG_COMBOBOX_SHORT

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


class PunctuationScene(AnimScene):
    """460x262 的单场景画布：设置行（真实控件）+ 竖排预览（自绘）。

    下拉选择叙事的组装样本：光标入场 → 按下展开下拉 → 移到菜单项点选 →
    标点移动 + 文案明暗互换。时间轴（@10fps → 2.5s）：
    F_OPEN=8 展开 / F_CLICK=12 点选 / 13～18 标点移动 / 20 光标淡出。
    """

    SIZE = (460, 262)
    N_FRAMES = 25
    ROW_Y = 18
    PANEL = (16, 58, 170, 182)  # x, y, w, h（竖排预览卡，模拟漫画页面）
    F_OPEN = 8
    F_CLICK = 12
    F_MOVE_START = 13
    F_MOVE_END = 18

    def _build(self):
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

        self.caption_labels = make_caption_labels(
            self, self.fg, CAPTIONS, CAPTION_X, CAPTION_Y, CAPTION_W, CAPTION_H
        )

        self._open_t = 0.0
        self._hover = -1
        self._move_t = 0.0
        self._guide_a = 0.0

        # 光标轨迹：入场（f/6）→ 下拉框 → 按下展开 → 移向菜单项（8 起步、
        # 12 到位）→ 点选。两处涟漪都钉在下拉框上（历史产物如此，保持）。
        self.plan = (
            CursorPlan(QPointF(self.width() - 30, self.height() - 26))
            .go(self._combo_click_point(), arrive=6)
            .press(7)
            .go(self._item_point(1), arrive=self.F_CLICK, start=self.F_OPEN)
            .press(self.F_CLICK, at=self._combo_click_point())
            .fade(20)
        )

    # ── 状态推进 ────────────────────────────────────────────────
    def _update(self, f: int):
        idx = 1 if f >= self.F_CLICK else 0
        if self.combo.currentIndex() != idx:
            self.combo.setCurrentIndex(idx)

        if self.F_OPEN <= f < self.F_CLICK:
            self._open_t = out_cubic((f - self.F_OPEN + 1) / 2.0)
        else:
            self._open_t = 0.0

        self._hover = 1 if self.F_OPEN + 3 <= f < self.F_CLICK else -1

        self._move_t = move_progress(f, self.F_MOVE_START, self.F_MOVE_END)
        self._guide_a = guide_alpha(f, self.F_MOVE_START, self.F_MOVE_END)
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
                    draw_cell_guide(
                        p, QRectF(col_cx - CELL / 2, cell_top, CELL, CELL),
                        self.accent, self._guide_a,
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

    def _paint_overlay_content(self, p: QPainter):
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


class CheckboxDemoScene(AnimScene):
    """复选框单点叙事的预设编排：设置行（真实 ConfigCheckBox）+ 预览卡 +
    右侧文案，光标入场点复选框后过渡。

    这只是最常见演示形态的**便捷预设**（全部由 anim_kit 组件组装而成，不是
    固定基类）：叙事不匹配的功能直接继承 ``AnimScene`` 自行组装（见
    ``PunctuationScene``）。子类填 ``CHECK_TEXT`` / ``CAPTIONS``（外观展示类
    置空并覆写 ``_build_captions`` 走单条文案）、覆写 ``_paint_content``
    （在预览白卡里作画）与 ``_advance``（按帧号推进自有状态），并按需调
    ``N_FRAMES`` 与时间轴类属性。光标与点击涟漪由 ``CursorPlan`` 统一驱动；
    ``_t`` 是预览的过渡进度，也是两条文案明暗互换的进度（``EMPH_SEG`` 可
    单独覆写，默认与 MOVE 段同步，见 ``set_caption_emphasis``）。
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
    EMPH_SEG = None              # 明暗互换帧段，None = 跟随 MOVE 段

    def _build(self):
        row_h = CONFIG_COMBOBOX_HEIGHT
        self.check = ConfigCheckBox(self.CHECK_TEXT)
        self.check.setParent(self)
        self.check.setFixedWidth(self.check.sizeHint().width() + 4)
        self.check.setGeometry(self.CHECK_X, self.ROW_Y, self.check.width(), row_h)
        self.check.setChecked(False)

        self._build_row_extra()
        self._build_captions()

        self._t = 0.0        # 场景主进度：0 = 关闭态，1 = 开启态
        self._emph = 0.0     # 文案明暗互换进度：0 = 改前条亮，1 = 改后条亮

        # 指示器 13x13（config/stylesheet.css 的 QCheckBox#ConfigCheckBox::indicator）
        self.plan = (
            CursorPlan(QPointF(self.width() - 30, self.height() - 26))
            .go(QPointF(self.CHECK_X + 10, self.ROW_Y + row_h / 2),
                arrive=self.F_ARRIVE)
            .press(self.F_PRESS)
            .press(self.F_CLICK)
            .fade(self.F_FADE)
        )

    # ── 供子类覆写 ──────────────────────────────────────────────
    def _build_row_extra(self):
        """在设置行里复选框之后追加真实控件（如 tcy 行的「应用」按钮）。"""

    def _build_captions(self):
        """文案区：默认行为对比类的双条 + 明暗互换；外观展示类覆写走单条。"""
        self.caption_labels = make_caption_labels(
            self, self.fg, self.CAPTIONS, CAPTION_X, CAPTION_Y, CAPTION_W,
            CAPTION_H,
        )

    def _advance(self, f: int):
        """按帧号推进场景自有状态（子类覆写）。"""

    def _paint_content(self, p: QPainter):
        """在预览白卡内作画（子类覆写）。"""

    # ── 状态推进 ────────────────────────────────────────────────
    def _update(self, f: int):
        checked = f >= self.F_CLICK
        if self.check.isChecked() != checked:
            self.check.setChecked(checked)
        self._t = move_progress(f, self.F_MOVE_START, self.F_MOVE_END)
        emph_seg = self.EMPH_SEG or (self.F_MOVE_START, self.F_MOVE_END)
        self._emph = move_progress(f, emph_seg[0], emph_seg[1])
        set_caption_emphasis(self.caption_labels, self._emph, self.fg)
        self._advance(f)

    # ── 绘制 ────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        px, py, pw, ph = self.PANEL
        p.setPen(QPen(self.border, 1))
        p.setBrush(QColor(255, 255, 255))
        p.drawRoundedRect(QRectF(px, py, pw, ph), 6, 6)
        self._paint_content(p)

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


class _VerticalColumnScene(CheckboxDemoScene):
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
        return guide_alpha(self._f, start, end)

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


class _BadgePageScene(CheckboxDemoScene):
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
        make_single_caption(self, self.fg, self.NOTE,
                            CAPTION_X, CAPTION_Y[0], CAPTION_W, CAPTION_H)
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
        make_single_caption(self, self.fg, self.NOTE,
                            CAPTION_X, CAPTION_Y[0], CAPTION_W, CAPTION_H)
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


class ClipTextScene(CheckboxDemoScene):
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
    # 时间轴（@10fps）：三拍。明暗互换跟「开关点开」走：改前条讲关闭态行为、
    # 改后条讲开启态行为，第三拍是过程、不另配文案
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
    EMPH_SEG = (F_CLICK, F_SHRINK_END)

    def _build(self):
        super()._build()
        self._font, self._fm = self._demo_font(self.FONT_PX)
        self._h = float(self.BOX[3])   # 当前块高（按帧推进）
        # 光标三拍：入场 → 复选框 → 角部手柄（拖拽期骑在手柄上随块移动，
        # go 的落点给 callable，pose 每帧现算）
        self.plan = (
            CursorPlan(QPointF(self.width() - 30, self.height() - 26))
            .go(QPointF(self.CHECK_X + 10,
                        self.ROW_Y + CONFIG_COMBOBOX_HEIGHT / 2),
                arrive=self.F_CB_ARRIVE)
            .press(self.F_CB_PRESS)
            .go(self._handle_center, arrive=self.F_HANDLE_PRESS - 1,
                start=self.F_HANDLE_MOVE_START - 1)
            .press(self.F_HANDLE_PRESS)
            .fade(self.F_FADE)
        )

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
    def _advance(self, f: int):
        self._h = self._height_at(f)

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

# 入库产物的钉定 DPR：仓库默认目录的产物固定按 1.25 渲染（与既有固化产物
# 同尺寸），不同工作机重出尺寸不漂；README 流程不受此约束
PINNED_DPR = 1.25


def _resolve_dpr(args):
    """产物 DPR 口径：写仓库默认目录 → 钉 ``PINNED_DPR``（None 返回值 = 本机）。"""
    if args.dpr:
        if args.dpr.lower() == "native":
            return None
        return float(args.dpr)
    if args.out:
        out_dir = osp.dirname(osp.abspath(args.out))
    elif args.out_dir:
        out_dir = args.out_dir
    else:
        out_dir = DEFAULT_OUT_DIR
    return (
        PINNED_DPR
        if osp.normcase(osp.abspath(out_dir)) == osp.normcase(osp.abspath(DEFAULT_OUT_DIR))
        else None
    )


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
    ap.add_argument("--dpr", default=None, metavar="NATIVE|数值",
                    help="渲染 DPR：默认自动——写仓库默认目录时钉 %.2f（入库"
                         "产物跨工作机尺寸一致），其余按本机屏幕；NATIVE 强制"
                         "本机真实 DPR，数字强制指定" % PINNED_DPR)
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

    # 钉 DPR 须先于 QApplication：关掉平台原生缩放再给全局缩放因子，任何
    # 屏幕缩放设置的机器上得到的 DPR 都是同一值（本机覆盖层走本机真实 DPR，
    # 覆盖层的意义就是本机适配）
    dpr_pin = _resolve_dpr(args)
    if dpr_pin is not None:
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        os.environ["QT_SCALE_FACTOR"] = str(dpr_pin)

    app = init_app(args.theme)

    keys = sorted(SCENES) if args.all else [args.scene]
    dpr = app.primaryScreen().devicePixelRatio() if app.screens() else 1.0
    mode = "钉定" if dpr_pin is not None else "本机"
    print(f"TOTAL {len(keys)}")
    print(f"生成 DPR {dpr:g}（{mode}；产物物理尺寸 = 460x262 逻辑 × DPR）")
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
            frames = render_frames(app, scene, dump_dir)
            out = args.out if (not args.all and args.out) else osp.join(
                args.out_dir or DEFAULT_OUT_DIR, f"{key}.webp"
            )
            size_kb = save_webp(frames, out, args.fps)
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
