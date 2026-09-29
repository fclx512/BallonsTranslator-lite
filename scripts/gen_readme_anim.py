"""README 大画幅演示动画生成器（独立流程，与弹层 gen_help_anim.py 共用 anim_kit）。

首个场景 format_tour：主页面右侧格式面板的基础排版巡礼（一镜到底）。

用法：
    ./ballontrans_pylibs_win/python.exe scripts/gen_readme_anim.py --scene format_tour
    可选：--out-dir 目录（默认 build/readme_anim）/ --fps 帧率（默认 20）/
         --lossy（有损编码）/ --dump-frames 目录 / --dpr 覆盖（默认钉 2.0）

与弹层流程的差异（README 大画幅约束）：
- 独立注册表，不进设置页「重新生成」流程；
- 画幅逐场景自声明（SIZE），输出视口固定尺寸、镜头按光标位置缓动跟随
  （camera_rect 钩子 + iter_frames 裁切）；
- 多拍长时长：CursorPlan 航点自由编排，帧数不设上限；
- 画布文本块走真机引擎（TextBlkItem + QGraphicsScene，与产品同渲染路径），
  竖排几何天然正确，不需要弹层场景的手绘探针验收；
- 编码默认无损（合成示意页为纯色内容），--lossy 可选有损控制体积。
"""

import argparse
import os
import os.path as osp
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
SCRIPT_DIR = osp.dirname(osp.abspath(__file__))
for _p in (SCRIPT_DIR, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from qtpy.QtCore import QPointF, QRectF, Qt
from qtpy.QtGui import QColor, QPainter, QPen, QPolygonF
from qtpy.QtWidgets import (
    QFrame,
    QGraphicsEllipseItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from anim_kit import (
    AnimScene,
    CursorPlan,
    UI_FONT_FAMILY,
    clamp01,
    init_app,
    iter_frames,
    move_progress,
    out_cubic,
    save_webp,
)
from ui.custom_widget.checkbox import QFontChecker
from ui.custom_widget.combobox import SizeComboBox
from ui.custom_widget.label import ColorPickerLabel, SizeControlLabel
from ui.text_panel import (
    AlignmentBtnGroup,
    FontFamilyComboBox,
    FontSizeBox,
    FontStyleComboBox,
    FormatGroupBtn,
)
from ui.textitem import TextBlkItem
from utils.textblock import TextBlock

# ── 画幅常量 ─────────────────────────────────────────────────────
VIEW_W, VIEW_H = 880, 560          # 输出视口（镜头裁切尺寸）
CANVAS = QRectF(16, 16, 744, 548)  # 画布区（示意漫画页）
PANEL = QRectF(776, 16, 328, 548)  # 右栏格式面板

DEMO_TEXT = "这是一段演示文字，样式随右侧面板调整"
PICK_COLOR = QColor(30, 147, 229)  # 演示选中的文字色（取主题蓝）

# 分镜帧号（20fps，共 210 帧 ≈ 10.5s）。节奏口径：只压光标移动段（约减半），
# 每拍点击后的静止展示时长与弹层版次版一致
F_SEL_PRESS = 7        # 点选文本块
F_COLOR_OPEN = 30      # 色板弹出
F_COLOR_PICK = 38      # 选色
F_COLOR_CLOSE = 46     # 色板收起
F_FAMILY_OPEN = 57     # 字体下拉展开
F_FAMILY_PICK = 65     # 选字体（KaiTi）
F_FAMILY_CLOSE = 73    # 下拉收起
F_ALIGN_PRESS = 84     # 居中对齐
F_ITALIC_PRESS = 92    # 斜体
F_VERT_PRESS = 113     # 竖排
F_DRAG0, F_DRAG1 = 145, 174   # 行距拖拽（显示静默刷新）
F_DRAG_COMMIT = 175    # 松手提交（块重排）
F_END = 184            # 收尾：光标淡出 + 镜头回全景
LINE_SPACING_FROM, LINE_SPACING_TO = 1.2, 1.85

# 字幕：帧号 → 文案（beat 起点淡入、下一拍前淡出）
CAPTION_BEATS = [
    (0, "选中文字块，右侧格式面板调整文本样式"),
    (23, "点色块选颜色，下拉列表换字体"),
    (77, "一键对齐与斜体"),
    (106, "一键竖排，标点自动摆位"),
    (137, "拖行距标签调值，松手生效"),
]


class FormatTourScene(AnimScene):
    SIZE = (1120, 580)
    N_FRAMES = 210

    # ── 版式搭建 ────────────────────────────────────────────────
    def _build(self):
        self._palette_open = False
        self._dropdown_open = False
        self._ls_value = LINE_SPACING_FROM

        self._build_page()
        self._build_panel()
        self._build_block()

        # 字幕（跟随镜头，每帧贴视口左下角）
        self.caption = QLabel(self)
        self.caption.setWordWrap(True)
        self.caption.setTextFormat(Qt.TextFormat.RichText)
        self.caption.setAlignment(
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft
        )

        self._build_plan()
        self._cam = QPointF(*self._panorama_target())

    def _build_page(self):
        """画布区：QGraphicsView + 合成示意页（白卡 + 气泡 + 灰条假文）。"""
        self.gfx = QGraphicsScene(0, 0, CANVAS.width(), CANVAS.height())
        self.view = QGraphicsView(self.gfx, self)
        self.view.setGeometry(CANVAS.toRect())
        self.view.setStyleSheet(
            "QGraphicsView { background: transparent; border: none; }"
        )
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)

        card = QGraphicsRectItem(0, 0, CANVAS.width(), CANVAS.height())
        card.setBrush(QColor(255, 255, 255))
        card.setPen(self._page_pen())
        self.gfx.addItem(card)

        # 主气泡（承载真机文本块）——靠画布右侧放：镜头右移到面板时块仍完整入镜
        balloon = QGraphicsEllipseItem(256, 34, 408, 200)
        balloon.setBrush(QColor(255, 255, 255))
        balloon.setPen(self._page_pen())
        self.gfx.addItem(balloon)
        tail = QGraphicsPolygonItem(QPolygonF([
            QPointF(360, 228), QPointF(330, 268), QPointF(386, 230),
        ]))
        tail.setBrush(QColor(255, 255, 255))
        tail.setPen(self._page_pen())
        self.gfx.addItem(tail)

        # 次气泡 + 灰条假文（纯氛围，无信息量）
        balloon2 = QGraphicsEllipseItem(430, 330, 260, 120)
        balloon2.setBrush(QColor(255, 255, 255))
        balloon2.setPen(self._page_pen())
        self.gfx.addItem(balloon2)
        for i in range(3):
            bar = QGraphicsRectItem(470, 362 + i * 26, 170 - i * 34, 10)
            bar.setBrush(QColor(210, 210, 210))
            bar.setPen(self._no_pen())
            self.gfx.addItem(bar)

    def _page_pen(self):
        pen = QPen(QColor(120, 120, 120), 1.5)
        return pen

    _NO_PEN = None

    def _no_pen(self):
        if FormatTourScene._NO_PEN is None:
            FormatTourScene._NO_PEN = QPen(Qt.PenStyle.NoPen)
        return FormatTourScene._NO_PEN

    def _build_panel(self):
        """右栏：基础排版三行（颜色|字体|字重 / 对齐|样式|竖排 / 字号|行距|字距）。"""
        panel = QWidget(self)
        panel.setGeometry(PANEL.toRect())
        panel.setObjectName("readmeFormatPanel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel.setStyleSheet(
            "QWidget#readmeFormatPanel {"
            f" background: {self.card.name()};"
            f" border: 1px solid {self.border.name()};"
            " border-radius: 6px; }"
        )
        self.panel = panel

        v = QVBoxLayout(panel)
        v.setContentsMargins(10, 12, 10, 12)
        v.setSpacing(10)

        # Row 1：[颜色 | 字体 | 字重]
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        self.colorPicker = ColorPickerLabel(panel, param_name="frgb")
        self.colorPicker.setFixedSize(26, 26)
        self.colorPicker.setPickerColor(QColor(0, 0, 0))
        self.familybox = FontFamilyComboBox(parent=panel)
        self.familybox.addItems(["Microsoft YaHei UI", "KaiTi", "SimHei"])
        self.stylebox = FontStyleComboBox()
        self.stylebox.addItems(["Regular", "Bold"])
        row1.addWidget(self.colorPicker)
        row1.addWidget(self.familybox, 3)
        row1.addWidget(self.stylebox, 2)
        v.addLayout(row1)

        # Row 2：[对齐 | 样式 | 竖排]
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self.alignGroup = AlignmentBtnGroup(panel)
        self.formatGroup = FormatGroupBtn(panel)
        self.verticalChecker = QFontChecker(panel)
        self.verticalChecker.setObjectName("FontVerticalChecker")
        self.tcyChecker = QFontChecker(panel)
        self.tcyChecker.setObjectName("FontTateChuYokoChecker")
        self.romanChecker = QFontChecker(panel)
        self.romanChecker.setObjectName("FontRomanAlignmentChecker")
        row2.addWidget(self.alignGroup)
        row2.addWidget(self.formatGroup)
        row2.addWidget(self.verticalChecker)
        row2.addWidget(self.tcyChecker)
        row2.addWidget(self.romanChecker)
        row2.addStretch(1)
        v.addLayout(row2)

        # Row 3：[字号 | 行距 | 字距]
        row3 = QHBoxLayout()
        row3.setSpacing(6)
        self.fontsizebox = FontSizeBox(panel)
        self.lineSpacingLabel = SizeControlLabel(
            panel, direction=1, transparent_bg=False
        )
        self.lineSpacingLabel.setObjectName("lineSpacingLabel")
        self.lineSpacingBox = SizeComboBox([0, 100], "line_spacing")
        self.lineSpacingBox.addItems(
            [str(v_) for v_ in [1.0, 1.2, 1.5, 2.0]]
        )
        self.lineSpacingBox.setValue(LINE_SPACING_FROM)
        self.letterSpacingLabel = SizeControlLabel(
            panel, direction=0, transparent_bg=False
        )
        self.letterSpacingLabel.setObjectName("letterSpacingLabel")
        self.letterSpacingBox = SizeComboBox([0, 10], "letter_spacing")
        self.letterSpacingBox.addItems(["1.0", "1.15", "1.3"])
        row3.addWidget(self.fontsizebox)
        ls = QHBoxLayout()
        ls.setSpacing(2)
        ls.addWidget(self.lineSpacingLabel)
        ls.addWidget(self.lineSpacingBox)
        row3.addLayout(ls)
        letter = QHBoxLayout()
        letter.setSpacing(2)
        letter.addWidget(self.letterSpacingLabel)
        letter.addWidget(self.letterSpacingBox)
        row3.addLayout(letter)
        v.addLayout(row3)
        v.addSpacing(6)

        # 占位示意：分隔线 + 效果栈折叠胶囊 + 灰条（本次巡礼不入镜的功能区）
        sep = QFrame(panel)
        sep.setObjectName("fmtGroupSeparator")
        sep.setFixedHeight(1)
        v.addWidget(sep)
        capsule = QLabel(panel)
        capsule.setText("Text Effects")
        capsule.setAlignment(Qt.AlignmentFlag.AlignCenter)
        capsule.setFixedHeight(28)
        capsule.setStyleSheet(
            f"color: rgba({self.fg.red()},{self.fg.green()},{self.fg.blue()},180);"
            f"background: {self.border.name()}; border-radius: 6px;"
            "font-size: 12px;"
        )
        v.addWidget(capsule)
        for i in range(2):
            bar = QLabel(panel)
            bar.setFixedHeight(12)
            bar.setStyleSheet(
                f"background: {self.border.name()}; border-radius: 3px;"
            )
            v.addWidget(bar)
        v.addStretch(1)

        # 字号框回显演示字号
        self.fontsizebox.fcombobox.setValue(34)

    def _build_block(self):
        """真机文本块（与产品同渲染路径）。"""
        blk = TextBlock(
            xyxy=[290, 50, 650, 210],
            language="CHN",
            translation=DEMO_TEXT,
        )
        blk.set_lines_by_xywh([290, 50, 360, 160])
        blk.fontformat.font_size = 34
        self.blk = blk
        self.item = TextBlkItem(blk, idx=0, set_format=True, show_rect=False)
        self.gfx.addItem(self.item)

    def _build_plan(self):
        """光标航点（按分镜帧号顺序）。

        航点取自子控件几何——必须先强制激活布局：QVBoxLayout 惰性激活，
        构造期所有子控件还停在 (0,0)（grab 时才补算），直接 mapTo 全部
        落到面板左上角、光标"原地蠕动"。
        """
        self.panel.layout().activate()

        def w(widget, dx=0.5, dy=0.5):
            pos = widget.mapTo(self, QPointF(
                widget.width() * dx, widget.height() * dy))
            return QPointF(pos)

        block_target = QPointF(470, 130)
        # 色板里 PICK_COLOR 在第 2 行第 2 格（见 _paint_palette 的格子布点）
        self._palette_cell_pos = self.colorPicker.mapTo(self, QPointF(0, 0)) \
            + QPointF(57, 91)
        self._kai_row_pos = self.familybox.mapTo(self, QPointF(13, 13)) \
            + QPointF(0, 66)
        drag_start = self.lineSpacingLabel.mapTo(self, QPointF(8, 8))

        self.plan = CursorPlan(QPointF(40, 640))
        self.plan.go(block_target, arrive=F_SEL_PRESS - 1)
        self.plan.press(F_SEL_PRESS, block_target)
        # press 后光标在落点停 2 帧再启程（start 压住段起点），点击指向性更强
        self.plan.go(w(self.colorPicker), arrive=F_COLOR_OPEN - 1,
                     start=F_SEL_PRESS + 2)
        self.plan.press(F_COLOR_OPEN)
        self.plan.go(self._palette_cell_pos, arrive=F_COLOR_PICK - 1,
                     start=F_COLOR_OPEN + 2)
        self.plan.press(F_COLOR_PICK)
        self.plan.go(w(self.familybox), arrive=F_FAMILY_OPEN - 1,
                     start=F_COLOR_PICK + 2)
        self.plan.press(F_FAMILY_OPEN)
        self.plan.go(self._kai_row_pos, arrive=F_FAMILY_PICK - 1,
                     start=F_FAMILY_OPEN + 2)
        self.plan.press(F_FAMILY_PICK)
        self.plan.go(w(self.alignGroup.alignCenterChecker),
                     arrive=F_ALIGN_PRESS - 1, start=F_FAMILY_PICK + 2)
        self.plan.press(F_ALIGN_PRESS)
        self.plan.go(w(self.formatGroup.italicBtn), arrive=F_ITALIC_PRESS - 1,
                     start=F_ALIGN_PRESS + 2)
        self.plan.press(F_ITALIC_PRESS)
        self.plan.go(w(self.verticalChecker), arrive=F_VERT_PRESS - 1,
                     start=F_ITALIC_PRESS + 2)
        self.plan.press(F_VERT_PRESS)
        self.plan.go(drag_start, arrive=F_DRAG0 - 1, start=F_VERT_PRESS + 2)
        self.plan.press(F_DRAG0)
        # 拖拽段：光标下行 84px，目标随帧移动
        drag_dy = {"v": 0.0}
        def drag_target():
            return QPointF(drag_start) + QPointF(0, drag_dy["v"])
        self._drag_dy = drag_dy
        self.plan.go(drag_target, arrive=F_DRAG1)
        self.plan.fade(F_END, frames=3)

    # ── 镜头 ────────────────────────────────────────────────────
    def _panorama_target(self):
        # 全景=画布区居中；负值钳到 0（否则裁切贴边界时输出尺寸会缩水）
        x = min(max(CANVAS.center().x() - VIEW_W / 2, 0), self.SIZE[0] - VIEW_W)
        y = min(max(CANVAS.center().y() - VIEW_H / 2, 0), self.SIZE[1] - VIEW_H)
        return (x, y)

    def _cam_target(self, f):
        pan = QPointF(*self._panorama_target())
        pose = self.plan.pose(f)
        fol = QPointF(
            pose.point.x() - VIEW_W / 2, pose.point.y() - VIEW_H / 2
        )
        fol.setX(min(max(fol.x(), 0), self.SIZE[0] - VIEW_W))
        fol.setY(min(max(fol.y(), 0), self.SIZE[1] - VIEW_H))
        t_open = out_cubic(clamp01((f - 8) / 10))
        t_end = out_cubic(clamp01((f - F_END) / (self.N_FRAMES - 1 - F_END)))
        t = t_open * (1.0 - t_end)
        return pan + (fol - pan) * t

    def camera_rect(self, f):
        return QRectF(self._cam.x(), self._cam.y(), VIEW_W, VIEW_H)

    # ── 状态推进 ─────────────────────────────────────────────────
    def _update(self, f):
        # 镜头平滑跟随（指数趋近，确定性）
        target = self._cam_target(f)
        self._cam += (target - self._cam) * 0.25

        # 节拍动作
        if f == F_SEL_PRESS:
            self.item.setSelected(True)
        elif f == F_COLOR_OPEN:
            self._palette_open = True
        elif f == F_COLOR_PICK:
            self.colorPicker.setPickerColor(PICK_COLOR)
            self.item.setFontColor(
                (PICK_COLOR.red(), PICK_COLOR.green(), PICK_COLOR.blue()))
        elif f == F_COLOR_CLOSE:
            self._palette_open = False
        elif f == F_FAMILY_OPEN:
            self._dropdown_open = True
        elif f == F_FAMILY_PICK:
            self.familybox.setCurrentIndex(1)
            self.item.setFontFamily("KaiTi")
        elif f == F_FAMILY_CLOSE:
            self._dropdown_open = False
        elif f == F_ALIGN_PRESS:
            self.alignGroup.setAlignment(1)
            self.item.setAlignment(1)
        elif f == F_ITALIC_PRESS:
            self.formatGroup.italicBtn.setChecked(True)
            self.item.setFontItalic(True)
        elif f == F_VERT_PRESS:
            self.verticalChecker.setChecked(True)
            self.item.setVertical(True)
        elif F_DRAG0 < f <= F_DRAG1:
            t = (f - F_DRAG0) / (F_DRAG1 - F_DRAG0)
            self._ls_value = LINE_SPACING_FROM + (
                LINE_SPACING_TO - LINE_SPACING_FROM) * out_cubic(t)
            # 产品语义：拖拽中只静默刷新显示（blockSignals 语义同
            # ui/custom_widget/combobox.py::SizeComboBox._apply_drag_value）
            self.lineSpacingBox.setValue(self._ls_value)
            self._drag_dy["v"] = 84.0 * t
        elif f == F_DRAG_COMMIT:
            self.item.setLineSpacing(self._ls_value)

        self._update_caption(f)

    def _update_caption(self, f):
        text, start = None, 0
        for i, (s, t) in enumerate(CAPTION_BEATS):
            if f >= s:
                text, start = t, s
                next_start = (
                    CAPTION_BEATS[i + 1][0] if i + 1 < len(CAPTION_BEATS)
                    else self.N_FRAMES
                )
        if text is None:
            return
        # 淡入 4 帧、下一拍前 4 帧淡出
        a_in = clamp01((f - start) / 4.0)
        a_out = 1.0 - clamp01((f - (next_start - 4)) / 4.0)
        alpha = min(a_in, a_out)
        # 深色半透明底 + 白字：亮/暗主题下都压得住底图，字号提一档保辨识度
        self.caption.setStyleSheet(
            f"color: rgba(255,255,255,{round(235 * alpha)});"
            f"background: rgba(20,22,26,{round(200 * alpha)});"
            "border-radius: 8px; padding: 7px 14px; font-size: 17px;"
        )
        if self.caption.text() != text:
            self.caption.setText(text)
        cam = self.camera_rect(f)
        self.caption.setGeometry(
            int(cam.x()) + 14, int(cam.y()) + VIEW_H - 56,
            VIEW_W - 28, 40,
        )

    # ── 覆盖层置顶内容（色板 / 字体下拉，grab 抓不到真弹层）────────
    def _paint_overlay_content(self, p):
        f = self._f
        if self._palette_open:
            e = move_progress(f, F_COLOR_OPEN, F_COLOR_OPEN + 4)
            self._paint_palette(p, e)
        if self._dropdown_open:
            e = move_progress(f, F_FAMILY_OPEN, F_FAMILY_OPEN + 4)
            self._paint_dropdown(p, e)

    def _paint_panel_card(self, p, rect):
        p.setPen(self.border)
        p.setBrush(QColor(self.card))
        p.drawRoundedRect(rect, 5, 5)

    def _paint_palette(self, p, e):
        anchor = self.colorPicker.mapTo(self, QPointF(0, 0))
        x, y = anchor.x() - 4, anchor.y() + 30
        w, h = 164, 84
        rect = QRectF(x, y, w * e, h * e)
        self._paint_panel_card(p, rect)
        colors = [QColor(0, 0, 0), QColor(255, 255, 255), QColor(200, 60, 60),
                  QColor(240, 160, 40), QColor(80, 170, 80),
                  QColor(PICK_COLOR), QColor(120, 90, 200), QColor(120, 120, 120)]
        cw, ch = 26, 26
        for i, c in enumerate(colors):
            cx = x + 12 + (i % 4) * (cw + 10)
            cy = y + 12 + (i // 4) * (ch + 10)
            cell = QRectF(cx, cy, cw * e, ch * e)
            p.setPen(QColor(150, 150, 150))
            p.setBrush(c)
            p.drawRoundedRect(cell, 4, 4)
            if c == PICK_COLOR:
                p.setPen(self.accent)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(cell.adjusted(-2, -2, 2, 2), 5, 5)

    def _paint_dropdown(self, p, e):
        anchor = self.familybox.mapTo(self, QPointF(0, 0))
        x = anchor.x()
        y = anchor.y() + self.familybox.height() + 2
        w = max(self.familybox.width(), 150)
        rows = ["Microsoft YaHei UI", "KaiTi", "SimHei"]
        rh = 28
        rect = QRectF(x, y, w, (rh * len(rows) + 8) * e)
        self._paint_panel_card(p, rect)
        for i, name in enumerate(rows):
            ry = y + 4 + i * rh
            if i == 1:  # 高亮 KaiTi 行（光标落点）
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(self.accent)
                p.setOpacity(0.85 * e)
                p.drawRoundedRect(QRectF(x + 3, ry, w - 6, rh), 3, 3)
                p.setOpacity(1.0)
            p.setPen(QColor(255, 255, 255) if i == 1 else self.fg)
            p.drawText(QPointF(x + 12, ry + rh * 0.68), name)
        p.setOpacity(e)


# ── CLI ──────────────────────────────────────────────────────────
SCENES = {"format_tour": FormatTourScene}
DEFAULT_OUT_DIR = osp.join(ROOT, "build", "readme_anim")


def main():
    args = _parse_args()
    # README 产物钉 DPR 2.0：高清屏阅读者多，跨机器重出尺寸不漂
    if args.dpr is not None:
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        os.environ["QT_SCALE_FACTOR"] = str(args.dpr)

    app = init_app()
    dpr = app.primaryScreen().devicePixelRatio() if app.screens() else 1.0
    keys = sorted(SCENES) if args.all else [args.scene]
    print(f"TOTAL {len(keys)}")
    print(f"生成 DPR {dpr:g}（视口 {VIEW_W}x{VIEW_H} 逻辑）")
    failures = []
    for key in keys:
        try:
            scene = SCENES[key]()
            scene.ensurePolished()
            app.processEvents()
            frames = iter_frames(
                app, scene,
                dump_dir=args.dump_frames,
                camera=scene.camera_rect,
            )
            out = osp.join(args.out_dir, f"{key}.webp")
            size_kb = save_webp(frames, out, args.fps,
                                lossless=not args.lossy)
            print(f"OK {key}  {VIEW_W}x{VIEW_H} x{dpr:g}  {size_kb:.1f} KB  "
                  f"{scene.N_FRAMES} frames @ {args.fps}fps")
        except Exception as exc:  # noqa: BLE001
            failures.append((key, exc))
            print(f"FAIL {key}  {exc}")
    if failures:
        for key, exc in failures:
            print(f"  {key}: {exc!r}")
        sys.exit(1)


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=sorted(SCENES), default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--fps", type=int, default=20)
    parser.add_argument("--lossy", action="store_true")
    parser.add_argument("--dump-frames", default=None)
    parser.add_argument("--dpr", type=float, default=2.0)
    args = parser.parse_args()
    if not args.all and args.scene is None:
        parser.error("需要 --scene 或 --all")
    return args


if __name__ == "__main__":
    main()
