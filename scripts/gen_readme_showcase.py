"""README 展示动画：精简版样式编辑演示（约 13 秒）。

专为 README.md 设计的精简动画，聚焦最核心的样式编辑功能：
- 颜色选择（视觉冲击力强）
- 字体切换（日漫翻译的核心需求）
- 竖排转换（最具特色的功能）

相比 format_tour（10.5秒完整巡礼），本版更紧凑、更聚焦、更适合首页展示。

用法：
    ./ballontrans_pylibs_win/python.exe scripts/gen_readme_showcase.py --out-dir build/readme_anim
"""

import argparse
import os
import os.path as osp
import sys

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
    CameraScene,
    CursorPlan,
    PopupDropdown,
    PopupPalette,
    activate_layout_tree,
    caption_alpha,
    clamp01,
    graphics_item_scene_rect,
    init_app,
    iter_frames,
    move_progress,
    out_cubic,
    save_webp,
    widget_scene_center,
    widget_scene_rect,
)
from ui.custom_widget.checkbox import QFontChecker
from ui.custom_widget.label import ColorPickerLabel
from ui.text_panel import FontFamilyComboBox
from ui.textitem import TextBlkItem
from utils.textblock import TextBlock

# ── 画幅常量 ─────────────────────────────────────────────────────
VIEW_W, VIEW_H = 880, 560
CANVAS = QRectF(16, 16, 744, 548)
PANEL = QRectF(776, 16, 328, 548)

DEMO_TEXT = "漫画翻译神器\n一键排版样式"
COLOR_BLUE = QColor(30, 147, 229)
COLOR_RED = QColor(220, 50, 50)

# ── 手绘弹层对象（绘制与光标落点同一份几何，见 anim_kit）──────────
PALETTE = PopupPalette(
    origin_off=QPointF(-6, 36),   # 卡片左上角相对色块控件左上角
    pad=12, cell=26, gap=10, cols=4,
    colors=[
        QColor(0, 0, 0), QColor(255, 255, 255), QColor(COLOR_RED),
        QColor(240, 160, 40), QColor(80, 170, 80), QColor(COLOR_BLUE),
        QColor(120, 90, 200), QColor(120, 120, 120),
    ],
    pick_idx=2,                   # 演示选中的那一格（红色）
    width=164, height=84,
    card_radius=6.0,
)
DROPDOWN = PopupDropdown(
    gap=3, pad=5, row_h=32, min_w=160, text_inset=14,
    items=["Microsoft YaHei UI", "KaiTi", "SimHei"],
    pick_idx=1,                   # 演示选中的那一行（KaiTi 高亮）
    baseline=0.65,
    card_radius=6.0, hl_dx=-1.0, hl_dw=-8.0, hl_radius=4.0, hl_alpha=0.88,
)

# 分镜帧号（20fps，共 260 帧 ≈ 13s）
F_INTRO_END = 40          # 开场标题停留
F_SEL_PRESS = 50          # 选中文本块
F_COLOR_OPEN = 68         # 色板弹出
F_COLOR_PICK = 76         # 选择红色
F_COLOR_CLOSE = 84        # 色板收起
F_FONT_OPEN = 100         # 字体下拉
F_FONT_PICK = 108         # 选择楷体
F_FONT_CLOSE = 116        # 下拉收起
F_VERT_PRESS = 136        # 切换竖排
F_VERT_SETTLE = 156       # 竖排动画完成
F_OUTRO_START = 180       # 开始结尾展示
F_END = 250               # 淡出开始
N_FRAMES = 260

# 字幕
CAPTIONS = [
    (0, "BallonsTranslator - 漫画翻译与样式编辑"),
    (44, "点击色块更换文字颜色"),
    (88, "下拉列表切换字体"),
    (120, "一键转换竖排，标点自动摆位"),
    (180, "轻松完成专业级漫画翻译"),
]


class ShowcaseScene(CameraScene):
    SIZE = (1120, 580)
    N_FRAMES = N_FRAMES
    VIEW_W, VIEW_H = VIEW_W, VIEW_H
    CANVAS = CANVAS

    def _build(self):
        self._palette_open = False
        self._dropdown_open = False

        self._build_page()
        self._build_panel()
        self._build_block()

        # 字幕
        self.caption = QLabel(self)
        self.caption.setWordWrap(True)
        self.caption.setTextFormat(Qt.TextFormat.RichText)
        self.caption.setAlignment(
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft
        )

        self._build_plan()
        self._init_camera()

    def _build_page(self):
        """画布区：简化版气泡设计"""
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

        # 白色背景卡
        card = QGraphicsRectItem(0, 0, CANVAS.width(), CANVAS.height())
        card.setBrush(QColor(255, 255, 255))
        card.setPen(self._page_pen())
        self.gfx.addItem(card)

        # 主气泡（居中偏右，方便竖排后仍在视野内）
        balloon = QGraphicsEllipseItem(200, 100, 480, 280)
        balloon.setBrush(QColor(255, 255, 255))
        balloon.setPen(self._page_pen())
        self.gfx.addItem(balloon)

        # 气泡尾巴
        tail = QGraphicsPolygonItem(QPolygonF([
            QPointF(340, 370), QPointF(300, 420), QPointF(370, 375),
        ]))
        tail.setBrush(QColor(255, 255, 255))
        tail.setPen(self._page_pen())
        self.gfx.addItem(tail)

    def _page_pen(self):
        return QPen(QColor(120, 120, 120), 2.0)

    _NO_PEN = None

    def _no_pen(self):
        if ShowcaseScene._NO_PEN is None:
            ShowcaseScene._NO_PEN = QPen(Qt.PenStyle.NoPen)
        return ShowcaseScene._NO_PEN

    def _build_panel(self):
        """右栏：仅保留核心控件"""
        panel = QWidget(self)
        panel.setGeometry(PANEL.toRect())
        panel.setObjectName("showcasePanel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel.setStyleSheet(
            "QWidget#showcasePanel {"
            f" background: {self.card.name()};"
            f" border: 2px solid {self.border.name()};"
            " border-radius: 8px; }"
        )
        self.panel = panel

        v = QVBoxLayout(panel)
        v.setContentsMargins(12, 16, 12, 16)
        v.setSpacing(12)

        # 标题
        title = QLabel("文本样式", panel)
        title.setStyleSheet(
            f"color: {self.fg.name()}; font-size: 18px; font-weight: bold;"
        )
        v.addWidget(title)

        # 颜色选择器
        color_row = QHBoxLayout()
        color_label = QLabel("颜色:", panel)
        color_label.setStyleSheet(f"color: {self.fg.name()}; font-size: 14px;")
        self.colorPicker = ColorPickerLabel(panel, param_name="frgb")
        self.colorPicker.setFixedSize(32, 32)
        self.colorPicker.setPickerColor(QColor(0, 0, 0))
        color_row.addWidget(color_label)
        color_row.addWidget(self.colorPicker)
        color_row.addStretch(1)
        v.addLayout(color_row)

        v.addSpacing(8)

        # 字体选择器
        font_label = QLabel("字体:", panel)
        font_label.setStyleSheet(f"color: {self.fg.name()}; font-size: 14px;")
        v.addWidget(font_label)
        self.familybox = FontFamilyComboBox(parent=panel)
        self.familybox.addItems(DROPDOWN.items)
        v.addWidget(self.familybox)

        v.addSpacing(12)

        # 竖排切换
        vert_row = QHBoxLayout()
        vert_label = QLabel("竖排:", panel)
        vert_label.setStyleSheet(f"color: {self.fg.name()}; font-size: 14px;")
        self.verticalChecker = QFontChecker(panel)
        self.verticalChecker.setObjectName("FontVerticalChecker")
        vert_row.addWidget(vert_label)
        vert_row.addWidget(self.verticalChecker)
        vert_row.addStretch(1)
        v.addLayout(vert_row)

        v.addStretch(1)

    def _build_block(self):
        """文本块：双行文本，方便展示效果"""
        blk = TextBlock(
            xyxy=[250, 150, 630, 350],
            language="CHN",
            translation=DEMO_TEXT,
        )
        blk.set_lines_by_xywh([250, 150, 380, 200])
        blk.fontformat.font_size = 48
        self.blk = blk
        self.item = TextBlkItem(blk, idx=0, set_format=True, show_rect=False)
        self.gfx.addItem(self.item)

    def _build_plan(self):
        """光标路径——所有落点由控件/弹层的实际几何推出（见
        ``scripts/anim_kit.py::widget_scene_center`` / ``PopupPalette`` /
        ``PopupDropdown`` / ``graphics_item_scene_rect``），不手填像素坐标。"""
        activate_layout_tree(self)

        self._palette_origin = PALETTE.origin(
            widget_scene_rect(self, self.colorPicker).topLeft())
        self._palette_cell_pos = PALETTE.pick_point(self._palette_origin)
        fb_rect = widget_scene_rect(self, self.familybox)
        self._kai_row_pos = DROPDOWN.row_point(
            fb_rect.topLeft(), fb_rect.height(), DROPDOWN.pick_idx,
            DROPDOWN.width(fb_rect.width()))

        block_target = graphics_item_scene_rect(self, self.view, self.item).center()

        self.plan = CursorPlan(QPointF(40, 500))
        # 开场：光标不可见
        self.plan.go(block_target, arrive=F_SEL_PRESS - 1, start=F_INTRO_END)
        self.plan.press(F_SEL_PRESS, block_target)
        # 每拍：按下后按共享时序（anim_kit.PRESS_SETTLE / MOVE_LEAD）移到下一点
        self.plan.move_after_press(
            widget_scene_center(self, self.colorPicker), F_COLOR_OPEN)
        self.plan.press(F_COLOR_OPEN)
        self.plan.move_after_press(self._palette_cell_pos, F_COLOR_PICK)
        self.plan.press(F_COLOR_PICK)
        self.plan.move_after_press(
            widget_scene_center(self, self.familybox), F_FONT_OPEN)
        self.plan.press(F_FONT_OPEN)
        self.plan.move_after_press(self._kai_row_pos, F_FONT_PICK)
        self.plan.press(F_FONT_PICK)
        self.plan.move_after_press(
            widget_scene_center(self, self.verticalChecker), F_VERT_PRESS)
        self.plan.press(F_VERT_PRESS)
        self.plan.fade(F_END, frames=4)

    def _cam_target(self, f):
        pan = QPointF(*self._panorama_target())
        pose = self.plan.pose(f)
        fol = QPointF(
            pose.point.x() - VIEW_W / 2, pose.point.y() - VIEW_H / 2
        )
        fol.setX(min(max(fol.x(), 0), self.SIZE[0] - VIEW_W))
        fol.setY(min(max(fol.y(), 0), self.SIZE[1] - VIEW_H))

        # 开场全景，然后跟随，结尾回到全景
        if f < F_INTRO_END:
            return pan
        t_follow = out_cubic(clamp01((f - F_INTRO_END) / 10))
        t_outro = out_cubic(clamp01((f - F_OUTRO_START) / 20))
        t = t_follow * (1.0 - t_outro)
        return pan + (fol - pan) * t

    def _update(self, f):
        # 镜头平滑跟随（基类 CameraScene）
        self._advance_camera(f)

        # 节拍动作
        if f == F_SEL_PRESS:
            self.item.setSelected(True)
        elif f == F_COLOR_OPEN:
            self._palette_open = True
        elif f == F_COLOR_PICK:
            self.colorPicker.setPickerColor(COLOR_RED)
            self.item.setFontColor((COLOR_RED.red(), COLOR_RED.green(), COLOR_RED.blue()))
        elif f == F_COLOR_CLOSE:
            self._palette_open = False
        elif f == F_FONT_OPEN:
            self._dropdown_open = True
        elif f == F_FONT_PICK:
            self.familybox.setCurrentIndex(1)
            self.item.setFontFamily("KaiTi")
        elif f == F_FONT_CLOSE:
            self._dropdown_open = False
        elif f == F_VERT_PRESS:
            self.verticalChecker.setChecked(True)
            self.item.setVertical(True)

        self._update_caption(f)

    def _update_caption(self, f):
        text, start = None, 0
        for i, (s, t) in enumerate(CAPTIONS):
            if f >= s:
                text, start = t, s
                next_start = (
                    CAPTIONS[i + 1][0] if i + 1 < len(CAPTIONS)
                    else self.N_FRAMES
                )
        if text is None:
            self.caption.setStyleSheet("color: transparent;")
            return

        # 淡入淡出
        alpha = caption_alpha(f, start, next_start, fade_in=6.0, fade_out=6.0)

        self.caption.setStyleSheet(
            f"color: rgba(255,255,255,{round(245 * alpha)});"
            f"background: rgba(15,17,21,{round(210 * alpha)});"
            "border-radius: 10px; padding: 10px 18px; font-size: 18px; font-weight: 500;"
        )
        if self.caption.text() != text:
            self.caption.setText(text)

        o = self._crop_origin()
        self.caption.setGeometry(
            int(o.x()) + 20, int(o.y()) + VIEW_H - 70,
            VIEW_W - 40, 50,
        )

    def _paint_overlay_content(self, p):
        f = self._f
        if self._palette_open:
            e = move_progress(f, F_COLOR_OPEN, F_COLOR_OPEN + 4)
            self._paint_palette(p, e)
        if self._dropdown_open:
            e = move_progress(f, F_FONT_OPEN, F_FONT_OPEN + 4)
            self._paint_dropdown(p, e)

    def _paint_palette(self, p, e):
        PALETTE.paint(p, self._palette_origin, e, border=self.border,
                      card=self.card, accent=self.accent)

    def _paint_dropdown(self, p, e):
        fb = widget_scene_rect(self, self.familybox)
        DROPDOWN.paint(p, fb.topLeft(), fb.height(), fb.width(), e,
                       border=self.border, card=self.card, accent=self.accent,
                       fg=self.fg)


# ── CLI ──────────────────────────────────────────────────────────
SCENES = {"showcase": ShowcaseScene}
DEFAULT_OUT_DIR = osp.join(ROOT, "build", "readme_anim")


def main():
    args = _parse_args()
    if args.dpr is not None:
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        os.environ["QT_SCALE_FACTOR"] = str(args.dpr)

    app = init_app()
    dpr = app.primaryScreen().devicePixelRatio() if app.screens() else 1.0
    print(f"生成 README 展示动画（DPR {dpr:g}）")

    try:
        scene = ShowcaseScene()
        scene._dpr = dpr
        scene.ensurePolished()
        app.processEvents()

        os.makedirs(args.out_dir, exist_ok=True)
        frames = iter_frames(
            app, scene,
            dump_dir=args.dump_frames,
            camera=scene.camera_rect,
        )
        out = osp.join(args.out_dir, "showcase.webp")
        size_kb = save_webp(frames, out, args.fps, lossless=not args.lossy)
        print(f"✓ 生成成功: {out}")
        print(f"  尺寸: {VIEW_W}x{VIEW_H} @ DPR {dpr:g}")
        print(f"  大小: {size_kb:.1f} KB")
        print(f"  时长: {N_FRAMES} 帧 @ {args.fps}fps ({N_FRAMES/args.fps:.1f}s)")
    except Exception as exc:
        print(f"✗ 生成失败: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--fps", type=int, default=20)
    parser.add_argument("--lossy", action="store_true")
    parser.add_argument("--dump-frames", default=None)
    parser.add_argument("--dpr", type=float, default=2.0)
    return parser.parse_args()


if __name__ == "__main__":
    main()
